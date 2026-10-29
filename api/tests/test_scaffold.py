import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_seed_is_idempotent(db):
    from app.seed import seed

    await seed()
    await seed()
    rows = (await db.execute(text(
        "SELECT m.user_id, m.workspace_id, m.role, w.verified FROM memberships m "
        "JOIN workspaces w ON w.id = m.workspace_id ORDER BY m.user_id"
    ))).all()
    assert [tuple(r) for r in rows] == [
        ("admin_u", "ws_unverified", "admin", False),
        ("admin_v", "ws_verified", "admin", True),
        ("member_u", "ws_unverified", "member", False),
        ("member_v", "ws_verified", "member", True),
    ]


async def _insert_media(db, frame_count: int = 3) -> uuid.UUID:
    media_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO media (id, workspace_id, type, source, frame_count, frames) "
        "VALUES (:id, 'ws_unverified', 'video', 'upload', :n, '[]'::jsonb)"
    ), {"id": media_id, "n": frame_count})
    return media_id


async def _insert_decision(db, media_id, status: str, scored: int, count: int) -> uuid.UUID:
    decision_id = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO decisions (id, media_id, status, frames_scored, frame_count, decided_by) "
        "VALUES (:id, :m, :s, :scored, :count, 'policy')"
    ), {"id": decision_id, "m": media_id, "s": status, "scored": scored, "count": count})
    return decision_id


async def test_db_refuses_published_without_decision(db):
    media_id = await _insert_media(db)
    with pytest.raises(DBAPIError, match="without a decision"):
        await db.execute(text("UPDATE media SET status = 'published' WHERE id = :id"), {"id": media_id})
    await db.rollback()


async def test_db_refuses_published_with_partial_decision(db):
    """The incident: only the first 8 frames were scored. That decision cannot publish."""
    media_id = await _insert_media(db, frame_count=60)
    decision_id = await _insert_decision(db, media_id, "published", scored=8, count=60)
    with pytest.raises(DBAPIError, match="missing or incomplete"):
        await db.execute(text(
            "UPDATE media SET status = 'published', decision_id = :d WHERE id = :id"
        ), {"id": media_id, "d": decision_id})
    await db.rollback()


async def test_db_refuses_published_with_non_publish_decision(db):
    media_id = await _insert_media(db)
    decision_id = await _insert_decision(db, media_id, "held", scored=3, count=3)
    with pytest.raises(DBAPIError, match="missing or incomplete"):
        await db.execute(text(
            "UPDATE media SET status = 'published', decision_id = :d WHERE id = :id"
        ), {"id": media_id, "d": decision_id})
    await db.rollback()


async def test_db_allows_published_with_complete_decision(db):
    media_id = await _insert_media(db)
    decision_id = await _insert_decision(db, media_id, "published", scored=3, count=3)
    await db.execute(text(
        "UPDATE media SET status = 'published', decision_id = :d WHERE id = :id"
    ), {"id": media_id, "d": decision_id})
    status = (await db.execute(text("SELECT status FROM media WHERE id = :id"), {"id": media_id})).scalar()
    assert status == "published"
    await db.rollback()


async def test_db_allows_only_four_statuses(db):
    media_id = await _insert_media(db)
    with pytest.raises(IntegrityError):
        await db.execute(text("UPDATE media SET status = 'approved' WHERE id = :id"), {"id": media_id})
    await db.rollback()


async def test_db_one_active_job_per_media(db):
    media_id = await _insert_media(db)
    insert = text("INSERT INTO jobs (media_id, workspace_id, lane) VALUES (:m, 'ws_unverified', 'video')")
    await db.execute(insert, {"m": media_id})
    with pytest.raises(IntegrityError):
        await db.execute(insert, {"m": media_id})
    await db.rollback()


async def test_db_scores_at_most_once(db):
    media_id = await _insert_media(db)
    insert = text("INSERT INTO scores (media_id, frames) VALUES (:m, '[]'::jsonb)")
    await db.execute(insert, {"m": media_id})
    with pytest.raises(IntegrityError):
        await db.execute(insert, {"m": media_id})
    await db.rollback()
