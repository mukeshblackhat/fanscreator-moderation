from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import engine
from app.logging import configure_logging, log
from app.routes import callback, health, media, metrics, review


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings.log_level)
    log.info("api_start", screening_mode=settings.screening_mode)
    yield
    await engine.dispose()


app = FastAPI(title="FansCreator moderation", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)
for module in (health, media, review, callback, metrics):
    app.include_router(module.router)
