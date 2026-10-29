# FansCreator content moderation service

Screens uploaded and AI generated images and videos so nothing prohibited reaches anyone, nothing explicit reaches an unverified workspace, and nothing goes live unchecked, even when the moderation provider is slow, down, or answers twice.

> Work in progress. This README grows with each commit. The full write up (incident, fix, before and after numbers, crash test, production plan, part 3 answers) lands in the final docs commit.

## Run it

```bash
cp .env.example .env
docker compose up
```

| Service | URL |
| --- | --- |
| API | http://localhost:8000 (health at `/health`) |
| Fake provider | http://localhost:8001 (`/_control`, `/_stats`, `/_reset`) |
| Review page | http://localhost:5173 |

Postgres and Redis are exposed on 5432 and 6379 for local tests. The API runs migrations and an idempotent seed on start: `ws_verified` (users `admin_v`, `member_v`) and `ws_unverified` (users `admin_u`, `member_u`).

## Tests

```bash
# API: needs a throwaway Postgres database (migrated to head automatically)
cd api && TEST_DATABASE_URL=postgresql+asyncpg://moderation:moderation@localhost:5432/moderation_test uv run pytest

# Fake provider
cd provider && uv run pytest

# Web
cd web && pnpm install && pnpm test
```

## Layout

```
api/        FastAPI app, Alembic migrations, screening worker, tests
provider/   Fake moderation provider
web/        React review queue
scripts/    Workload, report and crash test
docs/       decisions.md, ai-log.md, evidence/
```

Design choices are in [docs/decisions.md](docs/decisions.md).
