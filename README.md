# FansCreator content moderation service

This service screens uploaded and AI-generated images and videos. It has three jobs: nothing prohibited reaches anyone, nothing explicit reaches an unverified workspace, and nothing goes live unchecked, even when the moderation provider is slow, down, or answers twice.

I started by rebuilding last Tuesday's incident on purpose (a broken v0 with all five bugs), wrote tests that failed against it, and then fixed it one symptom at a time. Every number below comes from a run saved in [`docs/evidence/`](docs/evidence/).

| | v0 (the incident) | Fixed |
| --- | --- | --- |
| Media published unchecked | 30 | **0** |
| Refused prompts that reached the generator | 9 of 9 | **0 of 9** |
| Items whose final status is wrong | 30 | **0** |
| Items scored successfully more than once | some (2 calls for 1 upload in tests) | **0 of 722** |

## 1. How to run

```bash
cp .env.example .env
docker compose up
```

| Service | URL |
| --- | --- |
| API | http://localhost:8000 (`/health`, `/metrics/summary`) |
| Fake provider | http://localhost:8001 (`/_control`, `/_stats`, `/_reset`) |
| Review page | http://localhost:5173 |

If you already run Postgres or Redis locally, set `DB_PORT` and `REDIS_PORT` in `.env` to free ports.

`docker compose up` starts `db`, `redis`, `provider`, `api` (runs the migrations and an idempotent seed first), `worker` (2 replicas) and `web`. The seed creates `ws_verified` (users `admin_v`, `member_v`) and `ws_unverified` (users `admin_u`, `member_u`). `SCREENING_MODE=broken` in `.env` switches the workers to the deliberately unsafe v0.

### Develop with live reload

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api python /srv/scripts/demo_data.py --api http://localhost:8000 --provider http://provider:8001
```

The API and provider reload on save, the workers restart on any Python change, and the review page hot-reloads. `scripts/demo_data.py` fills both workspaces through the real pipeline (add `--outage` to also see held items recover by themselves). The review page has three tabs: the review queue, all media with readable reasons, and **Try it**, which has one-click test uploads and switches for the fake provider's mode (healthy, outage, 500s, slow, 429, bad answers).

### Tests

The API tests need a throwaway Postgres database and Redis. The harness migrates the database, then starts the real fake provider and the real API as subprocesses, so timeouts, `202`s and signed callbacks behave like they do in compose.

```bash
cd api
uv sync
TEST_DATABASE_URL=postgresql+asyncpg://moderation:moderation@localhost:5432/moderation_test \
  SCREENING_MODE=fixed uv run pytest -v           # 125 tests
cd ../provider && uv sync && uv run pytest        # 5 tests
cd ../web && pnpm install && pnpm test            # 7 tests
```

### Workload, report and crash test

With the stack running (compose or local), from `api/`:

```bash
uv run python ../scripts/workload.py --label "after (fixed)" --out ../docs/evidence/04-fixed-workload.json
uv run python ../scripts/report.py ../docs/evidence/02-broken-workload.json ../docs/evidence/04-fixed-workload.json
uv run python ../scripts/crash_test.py --out ../docs/evidence/05-crash-test.json   # stop the other workers first
```

### API

| Route | Who | Result |
| --- | --- | --- |
| `POST /media` | any member | `202 {"id", "status": "pending"}`; refused prompt `422 {"reasons": [...]}` |
| `GET /media/{id}` | members of the owning workspace | `200 {"id", "status", "reasons"}`; anyone else `404` |
| `GET /review` | admins | held media of their own workspace; members `403` |
| `POST /review/{id}/approve`, `/reject` | admins | `200`; members `403`; other workspace `404`; unscreened hold `409` |
| `POST /provider/callback` | provider | `200`; missing or wrong HMAC-SHA256 signature `401` |

Identity comes from `X-User-Id` and `X-Workspace-Id` (missing either is `401`).

Media input is tolerant. Extra fields are accepted, and `type` or `kind` both work. An image must have exactly 1 frame. `source` defaults to `upload`, and `generated` needs a `prompt`. A missing label scores 0.

```json
{"type": "video", "source": "generated", "prompt": "a cat surfing at sunset",
 "frames": [{"scores": {"explicit": 0.1}}, {"scores": {"explicit": 0.95}}]}
```

## 2. The failure

`api/app/screening/broken_handler.py` (`SCREENING_MODE=broken`) reproduces the incident on purpose:

1. A provider timeout is treated as a pass, and the media is published.
2. Only the first 8 frames of a video are scored.
3. Generation prompts are never checked.
4. A running job is redelivered after 2 s with no lock or lease, so a slow provider call gets made twice.
5. The review page shows a failed `/review` request as "Nothing to review".

v0 also wrote its decision rows as if it had covered every frame. That detail matters: a database check that trusts the decision row would not have caught it.

Evidence against v0:
- [`01-broken-tests.txt`](docs/evidence/01-broken-tests.txt): all 6 API incident tests and the review page error-state test fail; every other test passes.
- [`02-broken-workload.json`](docs/evidence/02-broken-workload.json): the incident replayed (30 s healthy, 40 s provider timeout with a 200-video burst, 60 s recovery). 30 items went live that should not have, and 9 of 9 refused prompts were accepted.

## 3. The fix

| Symptom | What changed | Why | Test |
| --- | --- | --- | --- |
| Uploads went live unchecked | A timeout is never a pass. Timeouts and 5xx retry 3 times with exponential backoff and full jitter, 429 honours `Retry-After` with its own budget of 10, and a malformed answer gets 2 tries. Running out of budget means `held`. A Redis circuit breaker stops calls after 5 failures in 30 s and probes every 10 s. | Every unknown resolves toward "not live" | `test_provider_timeout_never_publishes`, `test_recovery.py` |
| Late explicit frame went live | Every frame goes to the provider, and an answer must cover frames 0 to n-1 exactly. Videos over 30 frames use the signed async callback. Migration 0002 also makes the database refuse `published` unless stored scores cover every frame. | Decision rows can lie; stored scores can't | `test_explicit_only_on_frame_55_of_60...`, `test_minor_only_on_last_frame...`, `test_db_refuses_published_when_decision_lies_about_coverage` |
| Prompt for a 16-year-old reached the generator | The prompt gate runs before generation: normalise (NFKC, zero-width removal, split letters, digit substitutions), then rules per category, plus ages under 18 in digits or words. A refused prompt returns `422`, and the generator and provider get zero calls. | Stop intent before spending anything | `test_refused_prompt_never_reaches_generator_or_provider`, 30 cases in `test_prompt_gate.py` |
| Uploads scored 2 or 3 times | `FOR UPDATE SKIP LOCKED` claims with a lease and a fencing token. A `provider_calls` row is committed before every call. `scores` has its primary key on `media_id`, and callback receipts make a duplicate a no-op. | Score once, on purpose | `test_redelivered_job_calls_provider_once`, `test_duplicate_callback_is_a_no_op`, `test_stale_worker_write_is_fenced` |
| "Nothing to review" while 40 waited | The review page has three distinct states: loading, an error with the HTTP status and a Retry button, and "Nothing to review" only after a successful empty response. Live counters show pending, held, oldest wait and provider state. | A failure must never look like an empty queue | `ReviewQueue.test.tsx` |

Recovery needs no human. Outage holds are released once the provider has answered again, at up to 50 items per 2 s, taking turns across workspaces. Before there's any sign of recovery, one item is released every 30 s as a probe. Approving an unscreened hold returns `409`, because that would publish something nobody checked.

Fairness: images, videos and re-screens each have their own lane and worker concurrency. Within a lane, the next job comes from the workspace with the fewest running jobs, capped at 4 in flight. In `test_fairness.py`, a 30-video burst from one workspace doesn't delay the other workspace's video (under 2 s). With first-in-first-out ordering the same test fails at 6.0 s.

The other decisions are in [`docs/decisions.md`](docs/decisions.md).

## 4. Before and after

Same seeded workload, both runs. v0 ran on the schema it was written for (migration 0001).

| Metric | before (v0) | after (fixed) |
| --- | --- | --- |
| Unchecked published (should be 0) | 30 | 0 |
| &nbsp;&nbsp;published without a complete decision | 0 | 0 |
| &nbsp;&nbsp;published although the full-frame policy says no | 30 | 0 |
| Refused prompts accepted | 9 of 9 | 0 of 9 |
| Provider calls per upload (target 1.0) | 1.037 | 1.021 |
| Oldest waiting job, outage | 38.9 s | 21.8 s |
| Oldest waiting job, recovery | 45.1 s | 24.5 s |
| ws_unverified image p50 / p95, healthy | 0.10 s / 0.16 s | 0.08 s / 0.12 s |
| ws_unverified image p50 / p95, outage | 28.42 s / 43.62 s | 40.80 s / 50.77 s |
| ws_unverified image p50 / p95, recovery | 0.10 s / 8.71 s | 0.14 s / 11.02 s |
| Final status differs from the full-frame policy | 30 | 0 |

How to read it:
- **Calls per upload 1.021.** All 722 screened items were scored successfully exactly once. The 15 extra calls are timeouts sent at the start of the outage, before the breaker opened.
- **Image latency during the outage went up.** v0 was fast because it published images it never screened. The fix waits for the provider, so the number includes the outage itself.
- **Recovery p95 of 11 s.** Images uploaded right after the provider comes back wait out the breaker's 10 s open period.
- **"Published without a complete decision" reads 0 for v0.** That's because v0's decision rows claimed every frame was covered. The row below it re-runs the policy over every frame and shows the real damage.

### Following one item through the logs

Every log line is JSON with `workspace_id`, `media_id`, `job_id`, `attempt`, `lane` and `provider_call_id`, so one grep tells an item's whole story ([full trace](docs/evidence/trace-outage-recovery.txt)):

```
09:27:03.963 media_received       lane=image
09:27:06.074 provider_answered    lane=image    attempt=1 outcome=timeout duration_ms=2090
09:27:08.435 provider_answered    lane=image    attempt=2 outcome=timeout
09:27:12.241 provider_answered    lane=image    attempt=3 outcome=timeout
09:27:12.251 media_held           held_reason_kind=screening_unavailable
             (admin approve here returned 409)
09:27:13.272 rescreen_enqueued    (one probe while the provider is still down)
09:27:15.419 provider_answered    lane=rescreen attempt=1 outcome=timeout
09:27:15.431 media_held           held_reason_kind=screening_unavailable
09:27:43.329 rescreen_enqueued    (provider back)
09:27:43.418 provider_answered    lane=rescreen attempt=1 outcome=ok duration_ms=77
09:27:43.434 media_decided        status=published
```

## 5. Crash test

`scripts/crash_test.py` starts a worker with `CRASH_AFTER_PROVIDER=1`. That worker calls `os._exit(137)` right after the provider scores a 20-frame video, before anything is saved. The video has explicit content only on its last frame and sits in the unverified workspace. Result ([`05-crash-test.json`](docs/evidence/05-crash-test.json)):

- After the crash, the media is `pending` (invisible) and the ledger shows the call `in_flight`.
- A second worker reclaims the job when the 5 s lease runs out (6.0 s after the crash). It marks the first call `abandoned`, calls again, and decides `blocked`.
- It was never `published`. It cost 2 provider calls, and the scores were stored once.

**What it proves:** lease expiry and reclaim work. The fencing token works (also tested directly: a stale worker's write is a no-op). The ledger stays honest, and a dead worker can't publish anything unchecked.

**What it doesn't prove:**
- A real network partition, where the provider got our request and we never got the reply. The test only covers "we got the reply and died".
- An OOM kill in the middle of a Postgres commit.
- A Postgres failover.
- Clock skew on lease times. Leases use the database clock, which helps, but I haven't tested it.
- A provider without idempotency keys, where retrying after an unknown outcome gets billed twice.

## 6. Limits and what's unfinished

- `docker compose up` was verified from a clean build (`docker compose build --no-cache`) on macOS with Docker 29.6 and Compose 5.3. All services came up healthy, and a smoke run against the stack checked:
  - a clean image is published
  - a 60-frame video with explicit content on frame 55 is blocked in the unverified workspace and published in the verified one (over the async callback)
  - a refused prompt gets 422
  - workspace isolation (404), member review (403) and an unsigned callback (401)
  - an outage holds an upload, approving that hold gets 409, and the upload is published after recovery
  - unchecked published stays 0
  I did not re-run the full workload inside compose; that ran against the same code on local Postgres 16 and Redis 7.
- The prompt gate is rules only. It misses paraphrase and other languages, and it will over-block some innocent text. Production needs a classifier (see Part 3).
- The workspace in-flight cap is soft. Two simultaneous claims can briefly exceed it. It's about fairness, not safety.
- In a quiet outage, held items come back up to 30 s after the provider recovers (the probe window). With traffic, it's about 2 s.
- The callback watchdog will pay for a second call if a callback is just late (tested and documented).
- Malformed holds don't re-screen automatically. An admin can reject them but not approve them.
- `/metrics/summary` is unauthenticated. It returns counts only.

**Time spent:** about 3 hours of active work on 29 Oct 2026, spread across the day.

## 7. Production plan

**First 15 minutes: stop the harm.**
1. Turn on a hold-everything flag for new uploads (fail closed), and freeze publishing for anything without a decision row.
2. Unpublish media published during the outage window without a complete decision and full-frame scores. The query is the `unchecked_published` one from `/metrics/summary`.
3. Page on-call.
4. Pull the held backlog straight from the database for moderators while the review page is broken.

**Size it.**
- Count unchecked published by workspace.
- Reconcile our `provider_calls` ledger with the provider invoice to find double charges.
- List the generation requests that skipped the prompt gate.

**Roll out.**
- Put the fix behind a feature flag.
- Canary 5% of workspaces, then 25%, then 100%.
- Hold each step for 30 minutes of normal traffic plus one synthetic provider outage, and watch unchecked published (must stay 0) and calls per upload.

**Roll back if:**
- any media is published unchecked
- calls per upload go above 1.2
- the oldest waiting job is over 5 minutes
- the review API error rate goes above 1%

**Verify recovery.**
- Re-screen every item from the incident window.
- Check unchecked published is 0 and stays 0, and the held backlog drains.
- Check the provider invoice matches the ledger.
- Write the postmortem.

## 8. Part 3 answers

**Scoring in production.** A mix.

At low volume, a paid API: Hive visual moderation, or AWS Rekognition for moderation plus celebrity recognition for `real_person`. Later, a self-hosted classifier on GPUs scores every frame, and only the uncertain band (roughly 0.3 to 0.9) goes to the paid API as a second opinion. No single vendor covers all five prohibited categories well. Age and real-person checks in particular need a second model.

For 10,000 images and 1,000 one-minute videos a month at 1 frame per second, that's 10,000 + 60,000 = 70,000 frames:

| Option | Price used | Month |
| --- | --- | --- |
| AWS Rekognition moderation | $0.001 per image, $0.10 per video minute | about $110 |
| Hive visual moderation, frames as images | $3 per 1,000 requests | about $210 |
| Celebrity check for `real_person` | about $0.001 per frame with a face | up to about $70 |
| CSAM hash matching | vendor quote (for example Thorn Safer via Hive) | contract |

**Roughly $110 to $280 a month in scoring fees, plus a CSAM matching contract.** List prices were checked in October 2026, but they change, so confirm before committing.

I score every second at this volume because it's cheap and the incident happened because frames were skipped. Once frames cost real money, I'd collapse near-identical frames with a perceptual hash (or sample by scene change), so every second is still covered by its own score or one from a nearly identical frame.

**Prompt checks.**
1. Normalise: Unicode NFKC, character substitutions, spacing tricks, and translation to English.
2. Apply deterministic rules.
3. Run a safety classifier with our own category definitions (for example Llama Guard 4).
4. Check negative prompts, reference images and LoRAs too, not just the text.
5. Screen the output anyway, since a clean prompt can still produce a bad image.
6. Rate limit, then review, accounts that keep probing.

Every bypass found becomes a regression test.

**CSAM hash matching.** It runs first, on every upload and every generated output, before any other model or person sees the file (Thorn Safer via Hive, or Microsoft PhotoDNA).
- While it's down: fail closed. New media is quarantined as held with its own reason. Ordinary reviewers can't see it, admins can't approve it, and it's re-checked automatically when the service comes back.
- On a match: block, preserve evidence under legal hold, report to NCMEC's CyberTipline, and never show the file to ordinary reviewers.

**At 1 million uploads a day.**

What breaks first is provider cost and rate limits. About 12.8M frames a day would cost $13k to $38k a day API-only, and we'd hit 429s. Next comes the Postgres jobs table: every claim, lease and retry is an UPDATE, so dead tuples and claim latency grow.

Measure:
- 429 rate and provider p99
- calls and cost per upload
- queue age per lane
- `n_dead_tup` and autovacuum lag on `jobs`
- lock waits and p99 on the claim query

Change first:
- tiered scoring, with a self-hosted first pass and the paid API only for the uncertain band
- a perceptual-hash cache, so a re-uploaded file is scored once

Then move frame-level work to Redis Streams or SQS, while Postgres keeps one row per media decision.

## Layout

```
api/        FastAPI app, Alembic migrations, screening pipeline and worker, tests
provider/   Fake moderation provider
web/        React review queue
scripts/    workload.py, report.py, crash_test.py
docs/       decisions.md, ai-log.md, evidence/
```

How I used AI on this is in [AI.md](AI.md).
