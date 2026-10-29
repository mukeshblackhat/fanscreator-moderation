"""Test harness.

* A real PostgreSQL database (TEST_DATABASE_URL), migrated to head and seeded.
* The real fake provider and the real API run as subprocesses on test ports,
  so callbacks and timeouts behave like they do in compose.
* Workers run in-process (`workers` fixture), so a test controls exactly how
  many are running and can tweak settings such as the provider timeout.
* SCREENING_MODE picks the pipeline under test: `broken` (v0) or `fixed`.
"""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import tempfile

API_PORT = 18000
PROVIDER_PORT = 18001
REAL_PROVIDER_PORT = 18002
MEDIA_STORE = tempfile.mkdtemp(prefix="media-store-")
TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://moderation:moderation@localhost:5432/moderation_test"
)
# Must be set before any app module is imported.
os.environ["DATABASE_URL"] = TEST_DB
os.environ.setdefault("SCREENING_MODE", "fixed")
os.environ["PROVIDER_URL"] = f"http://localhost:{PROVIDER_PORT}"
os.environ["CALLBACK_URL"] = f"http://localhost:{API_PORT}/provider/callback"
os.environ["REAL_PROVIDER_URL"] = f"http://localhost:{REAL_PROVIDER_PORT}"
os.environ["MEDIA_STORE"] = MEDIA_STORE
os.environ["REAL_MODEL"] = "stub"  # deterministic stand-in: mostly red image = explicit 0.95
os.environ.setdefault("MAX_VIDEO_SECONDS", "20")
os.environ.setdefault("PROVIDER_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("PROVIDER_TIMEOUT_SECONDS", "4")
os.environ.setdefault("LOG_LEVEL", "WARNING")

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

API_DIR = Path(__file__).resolve().parents[1]
PROVIDER_DIR = API_DIR.parent / "provider"
REAL_PROVIDER_DIR = API_DIR.parent / "provider_real"
API_URL = f"http://localhost:{API_PORT}"
PROVIDER_URL = f"http://localhost:{PROVIDER_PORT}"
REAL_PROVIDER_URL = f"http://localhost:{REAL_PROVIDER_PORT}"
MODE = os.environ["SCREENING_MODE"]

ADMIN_V = {"X-User-Id": "admin_v", "X-Workspace-Id": "ws_verified"}
MEMBER_V = {"X-User-Id": "member_v", "X-Workspace-Id": "ws_verified"}
ADMIN_U = {"X-User-Id": "admin_u", "X-Workspace-Id": "ws_unverified"}
MEMBER_U = {"X-User-Id": "member_u", "X-Workspace-Id": "ws_unverified"}

TERMINAL = {"published", "held", "blocked"}


def pytest_sessionstart(session: pytest.Session) -> None:
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run([sys.executable, "-m", "alembic", *args], cwd=API_DIR, env=os.environ.copy(),
                       check=True, capture_output=True)


def pytest_report_header(config: pytest.Config) -> str:
    return f"SCREENING_MODE={MODE}  database={TEST_DB.rsplit('/', 1)[-1]}"


def _provider_python(folder: Path = PROVIDER_DIR) -> str:
    venv = folder / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _wait_healthy(url: str, proc: subprocess.Popen, name: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{name} exited early with code {proc.returncode}")
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{name} did not become healthy")


@pytest.fixture(scope="session", autouse=True)
def services():
    env = os.environ.copy()
    logs = API_DIR / ".pytest_cache"
    logs.mkdir(exist_ok=True)
    provider = subprocess.Popen(
        [_provider_python(), "-m", "uvicorn", "provider.main:app", "--port", str(PROVIDER_PORT)],
        cwd=PROVIDER_DIR, env=env, stdout=open(logs / "provider.log", "w"), stderr=subprocess.STDOUT)
    real = subprocess.Popen(
        [_provider_python(REAL_PROVIDER_DIR), "-m", "uvicorn", "realprovider.main:app", "--port", str(REAL_PROVIDER_PORT)],
        cwd=REAL_PROVIDER_DIR, env=env, stdout=open(logs / "real-provider.log", "w"), stderr=subprocess.STDOUT)
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(API_PORT)],
        cwd=API_DIR, env=env, stdout=open(logs / "api.log", "w"), stderr=subprocess.STDOUT)
    try:
        _wait_healthy(PROVIDER_URL, provider, "provider")
        _wait_healthy(REAL_PROVIDER_URL, real, "real provider")
        _wait_healthy(API_URL, api, "api")
        yield
    finally:
        for proc in (api, provider, real):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # the provider may be hanging on purpose (timeout mode)
                proc.kill()
                proc.wait()


@pytest.fixture(scope="session", autouse=True)
async def seeded(services):
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
    """In-process API client (no workers involved). Used for request-level checks."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
        yield c


@pytest.fixture
async def api():
    """Black-box client for the API subprocess (the one the provider calls back)."""
    async with httpx.AsyncClient(base_url=API_URL, timeout=10) as c:
        yield c


@pytest.fixture
async def provider():
    async with httpx.AsyncClient(base_url=PROVIDER_URL, timeout=10) as c:
        await c.post("/_reset")
        yield c
        await c.post("/_reset")


async def _clean_media() -> None:
    from app.db import SessionLocal

    async with SessionLocal() as session, session.begin():
        await session.execute(text(
            "TRUNCATE review_actions, callback_receipts, scores, provider_calls, jobs, decisions, media CASCADE"
        ))


@pytest.fixture
async def workers(provider):
    """Start N in-process workers for the configured mode on a clean queue.
    Usage: `await workers(2)`. All are cancelled when the test ends."""
    from app.screening import breaker, worker

    await _clean_media()
    await breaker.reset()
    tasks: list[asyncio.Task] = []

    async def start(n: int = 2) -> None:
        for i in range(n):
            tasks.append(asyncio.create_task(worker.run(f"test-worker-{len(tasks)}")))

    yield start
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def upload(api: httpx.AsyncClient, who: dict, body: dict) -> str:
    r = await api.post("/media", headers=who, json=body)
    assert r.status_code == 202, r.text
    return r.json()["id"]


async def wait_final(api: httpx.AsyncClient, who: dict, media_id: str, timeout: float = 30) -> tuple[str, list[str]]:
    """Poll until the media leaves `pending`. Returns (final status, every status seen)."""
    seen: list[str] = []
    deadline = time.monotonic() + timeout
    status = "pending"
    while time.monotonic() < deadline:
        status = (await api.get(f"/media/{media_id}", headers=who)).json()["status"]
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in TERMINAL:
            return status, seen
        await asyncio.sleep(0.1)
    return status, seen


def video(n: int, overrides: dict[int, dict[str, float]] | None = None) -> dict:
    frames = [{"scores": {}} for _ in range(n)]
    for i, scores in (overrides or {}).items():
        frames[i] = {"scores": scores}
    return {"type": "video", "frames": frames}


def image(scores: dict[str, float] | None = None) -> dict:
    return {"type": "image", "frames": [{"scores": scores or {}}]}
