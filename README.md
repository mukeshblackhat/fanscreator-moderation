# FansCreator content moderation service

This service screens uploaded and AI-generated images and videos. It has three jobs: nothing prohibited reaches anyone, nothing explicit reaches an unverified workspace, and nothing goes live unchecked, even when the moderation provider is slow, down, or answers twice.

I started by rebuilding last Tuesday's incident on purpose (a broken v0 with all five bugs), wrote tests that failed against it, and then fixed it one symptom at a time. Every number below comes from a run saved in [`docs/evidence/`](docs/evidence/).

| | v0 (the incident) | Fixed |
| --- | --- | --- |
| Media published unchecked | 30 | **0** |
| Refused prompts that reached the generator | 9 of 9 | **0 of 9** |
| Items whose final status is wrong | 30 | **0** |
| Items scored successfully more than once | some (2 calls for 1 upload in tests) | **0 of 723** |

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

The API and provider reload on save, the workers restart on any Python change, and the review page hot-reloads. `scripts/demo_data.py` fills both workspaces through the real pipeline (add `--outage` to also see held items recover by themselves).

The page has a **fake provider bar on every tab**. It switches the provider to healthy, slow (with a delay you set), outage, 500s, 429 or bad answers, toggles duplicate callbacks, and resets the counts. It uses the same `/_control` endpoint as the graders' checks. Below it are three tabs:
- **Review queue**
- **All media**, with readable reasons
- **Try it**, which has:
  - a prompt tester: prompt and negative-prompt fields, example cases (every category, the incident case, each bypass trick, and allowed adult prompts), the nine true or false fields, the rules that fired (admins), and Generate with a choice of output scores
  - real file uploads
  - one-click synthetic test media

### Real uploads (optional extra, beyond the task's scope)

The task says no real images and no real classifier, and the graded service follows that: by default everything goes to the fake provider. Real uploads are an extra I added to see the pipeline work on real pixels. They are off unless you turn them on. To screen real files as well, turn on the `real` profile (`COMPOSE_PROFILES=real` in `.env`) and upload from the **Try it** tab.

- **Primary scorer:** a local open-source image model (`Falconsai/nsfw_image_detection`, a ViT for normal vs nsfw). It runs on CPU in the `real-provider` container, so files never leave the machine.
- **Optional second opinion:** OpenAI `omni-moderation-latest`, if `OPENAI_API_KEY` is set. It adds self-harm, and sexual violence as a heuristic (sexual and violent at once). For explicit, it takes the higher of the two scores.
- **Never checked on real files:** age (`minor`), real person, and animals. Those need specialised models, and the minor category must never be tested with real images. Use the synthetic buttons for them.
- **How files are handled:** images are re-encoded to JPEG (max 1024 px, EXIF removed). Videos up to 2 minutes are sampled at 1 frame per second with ffmpeg, and every frame is scored. The real provider always answers through the signed callback. If anything fails while scoring, it reports an error instead of a partial answer; that is retried once and then held, never published.
- **Who can see frames:** published media is visible to members of its workspace, and score holds are visible to workspace admins for review. Pending, blocked and unscreened media is never served. Thumbnails of held or explicit media are blurred until clicked.

The first build downloads torch and the model (about 1 GB).

### Tests

The API tests need a throwaway Postgres database and Redis. The harness migrates the database, then starts the real fake provider and the real API as subprocesses, so timeouts, `202`s and signed callbacks behave like they do in compose.

```bash
cd api
uv sync
TEST_DATABASE_URL=postgresql+asyncpg://moderation:moderation@localhost:5432/moderation_test \
  SCREENING_MODE=fixed uv run pytest -v           # 215 tests
cd ../provider && uv sync && uv run pytest        # 6 tests
cd ../provider_real && uv sync && uv run pytest   # 7 tests (stub model, no downloads)
cd ../web && pnpm install && pnpm test            # 22 tests
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
| `POST /prompts/check`, `GET /prompts/events` | members (extra) | the prompt fields and decision without generating (rule hits for admins only); recent decisions of the workspace (no prompt text) |

Identity comes from `X-User-Id` and `X-Workspace-Id` (missing either is `401`).

Media input follows the task's format: each frame is a flat object of label scores, and generated media has `"source": "generation"` plus its `prompt`. A label left out scores 0. `source` defaults to `upload`, an image must have exactly 1 frame, and a score outside 0 to 1 is a `422`. For tolerance, `type` or `kind` both work, and the nested form `{"scores": {...}}` and `"generated"` are also accepted.

```json
{"type": "video", "source": "generation", "prompt": "two adults on a beach at sunset",
 "frames": [{"explicit": 0.02}, {"explicit": 0.95, "self_harm": 0.1}]}
```

That exact example is a test (`tests/test_task_format.py`): blocked in `ws_unverified` (explicit on frame 1), published in `ws_verified`.

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
| Uploads went live unchecked | A timeout is never a pass. Each failure type has its own limit: timeout 3 tries, 500 or no connection 4 (both with exponential backoff and full jitter), 429 5 (honouring `Retry-After`), malformed 2. Running out means `held`. A Redis circuit breaker stops calls after 5 failures in 30 s and probes every 10 s. | Every unknown resolves toward "not live" | `test_provider_timeout_never_publishes`, `test_recovery.py` |
| Late explicit frame went live | Every frame goes to the provider, and an answer must cover frames 0 to n-1 exactly. Videos over 30 frames use the signed async callback. Migration 0002 also makes the database refuse `published` unless stored scores cover every frame. | Decision rows can lie; stored scores can't | `test_explicit_only_on_frame_55_of_60...`, `test_minor_only_on_last_frame...`, `test_db_refuses_published_when_decision_lies_about_coverage` |
| Prompt for a 16-year-old reached the generator | A prompt checker runs on every generation prompt before anything else (details below). A refused prompt returns `422` and the generator and provider get zero calls. | Stop intent before spending anything | `test_refused_prompt_never_reaches_generator_or_provider`, `test_prompt_gate.py`, `test_prompt_scoring.py`, `test_prompt_engine.py` |
| Uploads scored 2 or 3 times | `FOR UPDATE SKIP LOCKED` claims with a lease and a fencing token. A `provider_calls` row is committed before every call. `scores` has its primary key on `media_id`, and callback receipts make a duplicate a no-op. | Score once, on purpose | `test_redelivered_job_calls_provider_once`, `test_duplicate_callback_is_a_no_op`, `test_stale_worker_write_is_fenced` |
| "Nothing to review" while 40 waited | The review page has three distinct states: loading, an error with the HTTP status and a Retry button, and "Nothing to review" only after a successful empty response. Live counters show pending, held, oldest wait and provider state. | A failure must never look like an empty queue | `ReviewQueue.test.tsx` |

**The decision** (`api/app/screening/policy.py`) checks every frame, and the first rule that matches wins:

| # | If any frame has | Status |
| --- | --- | --- |
| 1 | any prohibited label at 0.8 or more | blocked, in every workspace |
| 2 | explicit at 0.8 or more, in an unverified workspace | blocked |
| 3 | any prohibited label from 0.5 up to 0.8 | held for an admin |
| 4 | nothing above | published (marked explicit if explicit is 0.8 or more) |

Blocked beats held, and held beats published. 0.8 itself blocks and 0.5 itself holds. Explicit content alone never holds an item, so only rule 3 sends anything to a human.

**The prompt checker** (`api/app/screening/prompt_gate.py`) returns the same shape a production model would:

```
under_18_or_looks_young, sexual_content, non_consent_or_sexual_violence, animal_sexual_content,
self_harm, real_identifiable_person, incest, trafficking, tries_to_trick_the_checker   -> true / false
```

- **The decision:** refuse if any prohibited field is true, otherwise allow. There's no middle band: a prompt never sends its output to approval. The bias is towards refusing, because a false refusal is acceptable and a false pass is not.
- **`sexual_content` alone is not a refusal.** Adult explicit content is judged at the output check, where it's published in a verified workspace and blocked in an unverified one.
- **`tries_to_trick_the_checker` is recorded for account-level strikes.** Tricks that hide a prohibited request are refused anyway, because the request is found after normalising.
- **A refusal is `422 {"reasons": ["prompt:minor"]}`.** It names the category, never the word. The generator and provider get zero calls.
- **Normalising first:** Unicode NFKC, look-alike letters from other alphabets mapped to ASCII, zero-width characters removed, words split by spaces or dots joined, leetspeak mapped, stretched letters collapsed, and narrow misspellings caught ("chlid", "chld", but not "chili").
- **Rules per category,** including ages under 18 in digits or words, age and school-stage words, and combinations: an age-coded word plus a sexual word is refused, and so is "unconscious" plus a sexual word. A field is true at strength 0.5 or more.
- **The negative prompt is checked too.** Excluding "adult" or "mature" from a sexual prompt is asking for a minor.
- **Every decision is recorded in `prompt_events`** with a SHA-256 of the prompt and its length, never the text.
- **`POST /prompts/check`** returns the fields without generating anything. Workspace admins also see which rule fired; members never do.
- **Known limit:** rules can still be beaten by indirect wording, euphemisms and other languages. Production keeps this rule floor and adds a decision model on top, refusing if either says no, failing closed if the model is down, and keeping the output frame check as the backstop.

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
| Provider calls per upload (target 1.0) | 1.034 | 1.022 |
| Oldest waiting job, outage | 39.7 s | 21.8 s |
| Oldest waiting job, recovery | 44.1 s | 22.6 s |
| ws_unverified image to first outcome (held counts) p50 / p95, healthy | 0.09 s / 0.15 s | 0.08 s / 0.12 s |
| ws_unverified image to first outcome (held counts) p50 / p95, outage | 27.63 s / 43.14 s | 20.46 s / 21.31 s |
| ws_unverified image to first outcome (held counts) p50 / p95, recovery | 0.08 s / 7.55 s | 0.13 s / 9.10 s |
| ws_unverified image to final decision p50 / p95, outage | 27.63 s / 43.14 s | 41.43 s / 52.47 s |
| Final status differs from the full-frame policy | 30 | 0 |

How to read it:
- **The burst does not stop the other workspace's images, during the outage or after it.** During the outage every `ws_unverified` image reached an outcome (held) at 20.5 s p50 and 21.3 s p95. That narrow spread is the 20 s hold rule doing its job, not images waiting behind the 200 videos (v0 spread from 27.6 s to 43.1 s). In the healthy and recovery phases images are decided in about 0.1 s at p50.
- **Calls per upload 1.022.** All 723 screened items were scored successfully exactly once. The extra calls are timeouts sent at the start of the outage, before the breaker opened.
- **Time to final decision during the outage went up (41 s).** v0 was fast because it published images it never screened. The fix holds them and re-screens after recovery, so that number includes the outage itself.
- **Recovery p95 of about 9 s.** Images uploaded right after the provider comes back wait out the breaker's 10 s open period.
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
- A second worker reclaims the job when the 5 s lease runs out (5.8 s after the crash). It marks the first call `abandoned`, calls again, and decides `blocked`.
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
