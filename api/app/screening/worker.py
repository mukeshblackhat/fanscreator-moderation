"""Worker entrypoint: `python -m app.screening.worker`.

SCREENING_MODE=broken runs the deliberately unsafe v0 handler.
SCREENING_MODE=fixed runs the safe pipeline: separate loops per lane
(image, video, rescreen) with their own concurrency, plus the re-screen and
callback-watchdog loop.
"""
import asyncio
import os
import socket

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.logging import configure_logging, log


async def run(worker_id: str) -> None:
    """One worker loop for the configured mode. Tests run this in-process."""
    if settings.screening_mode == "broken":
        from app.screening import broken_handler

        await broken_handler.run(worker_id)
        return
    from app.screening import pipeline, rescreen

    lanes = [("image", settings.image_concurrency), ("video", settings.video_concurrency),
             ("rescreen", settings.rescreen_concurrency)]
    tasks = [asyncio.create_task(pipeline.lane_loop(lane, f"{worker_id}/{lane}{i}"))
             for lane, n in lanes for i in range(n)]
    tasks.append(asyncio.create_task(rescreen.loop()))
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()


async def main() -> None:
    configure_logging(settings.log_level)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    log.info("worker_start", worker_id=worker_id, screening_mode=settings.screening_mode)
    await run(worker_id)


if __name__ == "__main__":
    asyncio.run(main())
