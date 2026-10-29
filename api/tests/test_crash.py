"""Worker dies after the provider answered, before the save. Runs the same
script as docs/evidence/05-crash-test.json, against the test services."""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from conftest import API_URL, MODE, PROVIDER_URL, TEST_DB, _clean_media

pytestmark = pytest.mark.skipif(MODE != "fixed", reason="leases and fencing exist only in the fixed pipeline")
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "crash_test.py"


async def test_worker_crash_after_provider_never_publishes_unchecked(provider):
    from app.screening import breaker

    await _clean_media()
    await breaker.reset()
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--api", API_URL, "--provider", PROVIDER_URL,
         "--database-url", TEST_DB, "--lease", "3", "--log-dir", str(Path(__file__).parent.parent / ".pytest_cache")],
        capture_output=True, text=True, timeout=90, env=os.environ.copy())
    result = json.loads(proc.stdout)
    assert result["checks"] == {k: True for k in result["checks"]}, json.dumps(result, indent=2)
    assert result["provider_calls_total"] == 2  # a sync call lost to a crash has to be paid for again


async def test_stale_worker_write_is_fenced(db):
    """Two claims of the same job (the first lease expired). Only the newest
    token can write; the stale worker's finish is a no-op."""
    from app.screening import pipeline, queue

    await _clean_media()
    media_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO media (id, workspace_id, type, source, frame_count, frames) "
        "VALUES (:id, 'ws_verified', 'image', 'upload', 1, '[{\"scores\": {}}]'::jsonb)"), {"id": media_id})
    await db.execute(text("INSERT INTO jobs (media_id, workspace_id, lane) VALUES (:m, 'ws_verified', 'image')"),
                     {"m": media_id})
    await db.commit()

    first = await queue.claim("image")
    await db.execute(text("UPDATE jobs SET lease_until = now() - interval '1 second' WHERE id = :id"),
                     {"id": first.id})
    await db.commit()
    second = await queue.claim("image")
    assert second.id == first.id and second.lease_token != first.lease_token

    scores = [{"minor": 0.0, "sexual_violence": 0.0, "bestiality": 0.0, "self_harm": 0.0,
               "real_person": 0.0, "explicit": 0.0}]
    assert await pipeline.finalize(media_id, first.lease_token, scores, None, first.id) is None
    assert not await queue.reschedule(first, 0, "stale")  # stale worker cannot touch the job either
    assert await pipeline.finalize(media_id, second.lease_token, scores, None, second.id) == "published"
