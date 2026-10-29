"""Worker entrypoint: `python -m app.screening.worker`.

SCREENING_MODE=broken runs the deliberately unsafe v0 handler.
SCREENING_MODE=fixed runs the safe pipeline. Until the fix commits land it
claims nothing, so every upload stays pending and invisible. That is the
fail-closed default: no screening means nothing goes live.
"""
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
    if settings.screening_mode == "broken":
        from app.screening import broken_handler

        await broken_handler.run(worker_id)
        return
    log.warning("fixed_pipeline_not_built_yet", worker_id=worker_id,
                note="jobs stay pending until the fix commits; nothing is published")
    while True:
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
