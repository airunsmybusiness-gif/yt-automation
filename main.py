import logging
import os
import sys
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from orchestration.pipeline import Pipeline
from orchestration.scheduler import register_jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("main")

# ── Fail-fast on missing secrets ────────────────────────────────────────────
REQUIRED = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "ANTHROPIC_API_KEY",
    "API_SECRET",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
]
missing = [k for k in REQUIRED if not os.environ.get(k)]
if missing:
    log.error(f"Missing required env vars: {missing}")
    sys.exit(1)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline = Pipeline()
    register_jobs(scheduler, pipeline)
    scheduler.start()
    log.info("Scheduler started")
    yield
    scheduler.shutdown()
    log.info("Scheduler stopped")


app = FastAPI(title="YT Automation", lifespan=lifespan)

from api.routes import router  # noqa: E402 — after app created
app.include_router(router)
