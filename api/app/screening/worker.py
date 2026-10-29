"""Worker loop entrypoint: `python -m app.screening.worker`.

Scaffold only. The broken v0 handler and the fixed pipeline arrive in later
commits; for now the worker starts, checks the database, and idles so that
`docker compose up` brings up every service."""
import asyncio
import os
import socket

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.logging import configure_logging, log


async def main() -> None:
    configure_logging(settings.log_level)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    log.info("worker_start", worker_id=worker_id, screening_mode=settings.screening_mode)
    while True:
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
