# Decisions

Each entry: what I chose, why, and what I gave up. When the task was ambiguous I picked the safer option.

## 1. Job queue lives in PostgreSQL, not Redis
The media row and its screening job are inserted in the same transaction, so a job can't be lost between two systems. Workers claim with `FOR UPDATE SKIP LOCKED`. Redis is only used for things that are fine to lose: the circuit breaker, the provider rate limiter and short-lived locks.
Trade-off: heavy update churn on the jobs table at very high volume. Fine at this scale; see README part 3.

## 2. "Published needs a decision" is enforced by a trigger
A `BEFORE INSERT OR UPDATE` trigger on `media` refuses `status = 'published'` unless `media.decision_id` points to a decision for the same media with `status = 'published'` and `frames_scored = frame_count`. Application bugs can't bypass it. A finished job is never treated as proof of screening.

## 3. Decisions are history, not one row per media
The starter plan suggested `decisions.media_id` unique. I made decisions append-only (indexed, not unique) and point `media.decision_id` at the current one. Reason: a score hold gets a `held` policy decision, then an admin approval writes a second decision (`decided_by = user id`). Keeping both is the audit trail. The publish trigger checks the decision that `decision_id` points to, so the guarantee is unchanged.

## 4. Other database guarantees
* `jobs`: partial unique index, at most one non-terminal job per media.
* `scores`: primary key on `media_id`, so a media item is scored successfully at most once.
* `callback_receipts`: primary key on `provider_job_id`, so a duplicate callback is a no-op.
* `media`: `status` limited to the four states; `held` if and only if `held_reason_kind` is set; images have exactly one frame; generated media must carry its prompt.

## 5. Migrations are hand-written SQL
The schema relies on a trigger, partial indexes and a deferrable foreign key. Writing it as SQL in one Alembic revision is clearer than autogenerate. The ORM models mirror it for readability.

## 6. Alembic uses psycopg (sync), the app uses asyncpg
Alembic's online mode is simplest with a sync driver. `migrations/env.py` swaps `+asyncpg` for `+psycopg` in the same `DATABASE_URL`.

## 7. Seed runs on every API start
`python -m app.seed` uses upserts, so it is idempotent and safe in the compose start command.

## 8. Dependencies
| Package | Why |
| --- | --- |
| fastapi, uvicorn | Required stack |
| pydantic, pydantic-settings | Request validation and env config |
| sqlalchemy[asyncio], asyncpg | Required async DB access |
| alembic, psycopg[binary] | Migrations (sync driver for Alembic only) |
| redis | Circuit breaker and rate limiter |
| httpx | Provider client with explicit timeouts; also the ASGI test client |
| structlog | JSON log lines with bound context |
| pytest, pytest-asyncio | Tests |
| react, @tanstack/react-query, vite | Required web stack |
| vitest, @testing-library/*, jsdom, msw | Frontend tests, including the failed-request state |

## 9. Fake provider extras
Beyond the required modes (`ok`, `slow`, `timeout`, `error`) the fake provider also has `rate_limit` (429 with `Retry-After: 2`), `malformed` (drops a label and a frame), `duplicate_callbacks`, `callback_delay_ms` and `POST /_reset`. They exist only so each failure path can be tested.

## 10. The broken v0 handler lies in its decision row
`broken_handler.py` records `frames_scored = frame_count` even when it scored 8 frames or none at all (timeout). That is how v0 behaved, and it is why the database guard alone is not enough: the trigger trusts the decision row. The fix will also require a `scores` row that covers every frame before a policy decision can publish.

## 11. Until the fixes land, fixed mode fails closed
Between commits, `SCREENING_MODE=fixed` claims no jobs (uploads stay `pending`, which nobody else can see) and the placeholder prompt gate refuses every prompt. An unfinished safe path should do nothing, not something unsafe.

## 12. Identity uses its own database session
The identity check runs in its own short session. Sharing the route's session auto-began a transaction, so the route's `session.begin()` failed.

## 13. Non-members get 404 on upload too
The task fixes 404 for media reads and 403 for review actions. For `POST /media` by a non-member I also return 404, so the API never confirms that a workspace exists.

## 14. Generator is a counted stub
There is no real model. `app/generator.py` counts and logs calls, so tests can prove a refused prompt never reaches it.

## 15. How "unchecked published" is counted
The workload counts a media item as unchecked published if it is published and either (a) its current decision does not cover every frame, or (b) the policy run over all of its true frame scores says it should not be published (this also covers refused prompts that slipped through). (b) is what catches v0, because v0's decision rows lied about coverage.

## 16. Test harness runs real services
Tests start the real fake provider and the real API as subprocesses on ports 18001 and 18000, so timeouts, 202s and signed callbacks behave like they do in compose. Workers run in-process so a test controls how many there are. `SCREENING_MODE` picks which pipeline the suite runs against.

## 17. Symptoms 1, 2 and 4 are fixed in one commit
Fail closed, every frame scored, and score once all live in the same pipeline code (claim, call, record, decide). Splitting them would have meant writing throwaway intermediate versions of that pipeline, so they landed together in `fix: fail closed ...`. Prompts (3) and the review page (5) have their own commits.

## 18. Retry budgets are counted from the ledger, per job
Timeouts and 5xx share a budget of 3, 429 has its own budget of 10, malformed answers get 2. The counts come from `provider_calls` rows for that job, not from an in-memory counter, so a worker crash or a redelivery cannot reset them. A re-screen creates a new job and so gets a fresh budget.

## 19. Malformed holds are not re-screened automatically
A malformed answer means the provider is broken in a way a retry did not fix. It is held as `provider_malformed`, logged at error level, and cannot be approved (409). An admin can still reject it. Automatic re-screen is only for `screening_unavailable`.

## 20. Re-screen needs evidence the provider is back
The loop releases a batch only of items held before the provider's last successful answer (kept in Redis). With no success since, it releases one item per 30 s window as a probe. Without this, a quiet outage (too little traffic to trip the breaker) would turn into a loop of paid retries.

## 21. The breaker fails open if Redis is down
The breaker only saves money and load. Every outcome is still validated and every hold is written to Postgres, so allowing calls when Redis is unreachable cannot publish anything unchecked.

## 22. Callback watchdog trades a possible second call for liveness
An async video with no callback after `CALLBACK_TIMEOUT_SECONDS` (120 s) is resubmitted. If the first callback was only late, both arrive: scores are stored once (primary key), the second is recorded as a receipt and ignored, and we paid twice. The test `test_missing_callback_is_resubmitted_and_scored_once` makes that cost visible.

## 23. The workspace in-flight cap is soft
Two workers claiming at the same instant can both see a workspace at 3 running jobs and both claim, giving 5. The cap is about fairness, not safety, so I accepted that instead of serialising claims.

## 24. Publishing also needs stored scores for every frame (migration 0002)
After this migration, `published` requires a `scores` row with exactly `frame_count` frames, as well as a complete decision. That is the check that would have caught v0's lying decision rows. A side effect: v0 can no longer publish at all on the migrated schema, which is the point.

## 25. Prompt gate: rules now, classifier later
Normalise (NFKC, lowercase, zero-width removal, joined split letters, digit and symbol substitutions, long repeats), then short synthetic term lists per category and age patterns under 18 in digits or words. Known limits: no translation, paraphrase gets through, and some innocent text will be over-blocked. Production would add a safety classifier with our own category definitions, check negative prompts, reference images and LoRAs, and rate limit accounts that keep probing. The output of a clean prompt is screened like any upload either way.

## 26. A re-screen gets one try
A re-screen job is itself a probe of a provider that just failed, so its timeout/5xx budget is 1, not 3. During a long quiet outage each probe costs one call, not three. Normal jobs keep the budget of 3.

## 27. Quiet outages recover within the probe window
If traffic is low during an outage, nothing proves the provider is back until the next probe, at most 30 s (`RESCREEN_PROBE_WINDOW_SECONDS`) after it recovers. With real traffic, the first successful answer releases held items on the next tick (5 s). Shorter windows recover faster but pay for more blind probes.

## 28. /metrics/summary is internal and unauthenticated
It returns counts only (no media ids, no content). In production it would sit behind the internal network or an admin token.

## 29. Re-screen pace: 50 items every 2 s
Measured with the workload: about 360 items were held during the 40 s outage. At the earlier 20 per 5 s, recovery took about 90 s; at 50 per 2 s (about 25 a second, still far below a provider rate limit) everything was back within the recovery phase. Two re-screen workers per process.

## 30. Crash hook is an env flag, off by default
`CRASH_AFTER_PROVIDER=1` makes a worker call `os._exit(137)` right after a successful provider answer, before anything is recorded. It exists only for `scripts/crash_test.py`. Compose never sets it.

## 31. Real uploads are an opt-in profile with their own provider
The task asks for no real images, so the graded path keeps the fake provider. Real uploads go to a second service, `provider_real`, which has the same `/score` contract. Nothing in the safety pipeline changed: the worker sends frames that carry an `image` path to the real provider, and everything else (retries, breaker, holds, recovery, every frame counting) applies as before. The service sits behind the `real` Compose profile, because torch and the model add about 1 GB.

## 32. What real mode can and cannot check
The local model only knows normal vs nsfw, so it supplies `explicit`. OpenAI moderation, when a key is set, adds self-harm and a sexual-violence heuristic. Age, real person and animals come back as 0 on real files and the page says so. A real platform needs specialised models for those, plus CSAM hash matching in front of everything. The minor category is never to be tested with real images.

## 33. The real provider always answers by callback and fails closed
CPU scoring can take longer than the 2 s sync timeout, so the real provider always returns 202 and posts a signed callback. If any frame fails (unreadable file, OpenAI error), the callback carries an error and no frames. The API counts that as a malformed answer: one retry, then held as `provider_malformed`. A partial answer is never used.

## 34. Frames are served by role and status, never for blocked media
`GET /media/{id}/frames/{n}`: published is visible to members of that workspace, and score holds to its admins (who have to see an item to review it). Pending, blocked and unscreened media returns 404 to everyone. Responses are `private, no-store`. Originals are deleted after frame extraction, so only the normalised frames that were scored are kept.

## 35. The task's media format, and why both forms are accepted
The task page shows frames as flat score objects (`{"explicit": 0.95, "self_harm": 0.1}`) and generated media as `"source": "generation"`. The build started from a transcription that used `{"scores": {...}}` and `"generated"`. The API now treats the task's format as primary and still accepts the nested form and `"generated"`, normalising both at the edge. Internally frames are stored as `{"scores": {...}}` (real uploads add an `image` path to the same object), so the pipeline did not change. A flat frame can never be read as all zeros: scores are any numeric keys, each must be 0 to 1, and anything else is a 422.

## 36. Prompts are scored, with a review band, not just matched
The first prompt gate was a yes/no term list. It now scores each category 0 to 1 with weighted rules, on the same thresholds as media: 0.8 refuses (422 before generating), 0.5 to 0.8 generates but holds the output for a human (`review:prompt_<category>`), below 0.5 is allowed. The review band is for prompts that are suspicious but not clear-cut ("school uniform", "young" next to "nude", step-family next to "naked"). Refusing those outright would over-block; allowing them silently would trust the output check alone. Holding the output lets a human decide, and it never softens a blocking output: if the output scores 0.9 on a prohibited label it is still blocked. The 422 body stays exactly `{"reasons": [...]}` as the task requires.

## 37. The negative prompt is part of the check
A negative prompt that excludes adulthood ("adult, mature, wrinkles") next to a sexual prompt is a known way of asking for a minor without saying so. That scores minor 0.9 (refuse). Excluding children in a negative prompt is what a negative prompt is for, so that alone is never penalised.

## 38. Prompt audit stores a hash, not the text
`prompt_events` records every decision (generation and checks) with a SHA-256 of the prompt and its length. That is enough to rate limit and review accounts that keep probing, without storing the text of refused requests. In production, refused prompts in the minor category would be preserved under legal hold for reporting, which is a policy and legal decision, not something to do by default in a demo.

## 39. Provider controls live on every tab
Switching the fake provider's state (healthy, slow with a delay, outage, 500s, 429, bad answers, duplicate callbacks, reset) is a page-wide bar, not part of Try it, so any state can be set while watching the review queue or the media list. It calls the provider's own `/_control`, the same endpoint the graders use.
