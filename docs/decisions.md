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
