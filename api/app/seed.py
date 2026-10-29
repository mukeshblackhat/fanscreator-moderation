"""Idempotent seed: two workspaces and four users. Safe to run on every start."""
import asyncio

from sqlalchemy import text

from app.db import SessionLocal, engine
from app.logging import configure_logging, log

WORKSPACES = [("ws_verified", True), ("ws_unverified", False)]
MEMBERSHIPS = [
    ("admin_v", "ws_verified", "admin"),
    ("member_v", "ws_verified", "member"),
    ("admin_u", "ws_unverified", "admin"),
    ("member_u", "ws_unverified", "member"),
]


async def seed() -> None:
    async with SessionLocal() as session, session.begin():
        for ws_id, verified in WORKSPACES:
            await session.execute(
                text(
                    "INSERT INTO workspaces (id, verified) VALUES (:id, :verified) "
                    "ON CONFLICT (id) DO UPDATE SET verified = EXCLUDED.verified"
                ),
                {"id": ws_id, "verified": verified},
            )
        for user_id, ws_id, role in MEMBERSHIPS:
            await session.execute(
                text("INSERT INTO users (id) VALUES (:id) ON CONFLICT DO NOTHING"), {"id": user_id}
            )
            await session.execute(
                text(
                    "INSERT INTO memberships (user_id, workspace_id, role) VALUES (:u, :w, :r) "
                    "ON CONFLICT (user_id, workspace_id) DO UPDATE SET role = EXCLUDED.role"
                ),
                {"u": user_id, "w": ws_id, "r": role},
            )
    log.info("seed_complete", workspaces=len(WORKSPACES), memberships=len(MEMBERSHIPS))


async def _main() -> None:
    configure_logging()
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
