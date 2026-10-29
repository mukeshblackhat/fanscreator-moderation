"""Tests run against a real PostgreSQL. Set TEST_DATABASE_URL to point at a
throwaway database; it is migrated to head and re-seeded for the session."""
import os
import subprocess
import sys
from pathlib import Path

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://moderation:moderation@localhost:5432/moderation_test"
)
os.environ["DATABASE_URL"] = TEST_DB  # must happen before app modules are imported

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]


def pytest_sessionstart(session: pytest.Session) -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DB}
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=API_DIR, env=env, check=True,
                       capture_output=True)


@pytest.fixture(scope="session", autouse=True)
async def seeded():
    from app.seed import seed

    await seed()
    yield
    from app.db import engine

    await engine.dispose()


@pytest.fixture
async def db():
    from app.db import SessionLocal

    async with SessionLocal() as session:
        yield session


@pytest.fixture
async def client():
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
        yield c


def headers(user: str, workspace: str) -> dict[str, str]:
    return {"X-User-Id": user, "X-Workspace-Id": workspace}


__all__ = ["headers", "text"]
