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

# ── Fail-fast on missing secrets ─────────────────────────────────────────────
REQUIRED = [
    # Supabase
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    # AI keys
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    # GCP
    "GCP_PROJECT_ID",
    "GCP_SERVICE_ACCOUNT_JSON",  # SA key JSON string for Railway ADC
    # GCS bucket
    "ASSETS_BUCKET",
    # Cloud Function URLs
    "CF_TTS_URL",
    "CF_IMAGE_URL",
    "CF_RENDER_URL",
    "CF_UPLOAD_URL",
    # API security (for manual trigger endpoints)
    "API_SECRET",
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


app = FastAPI(title="YT Automation — MindSeam", lifespan=lifespan)

from api.routes import router  # noqa: E402
app.include_router(router)
