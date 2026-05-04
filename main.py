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
    "GEMINI_API_KEY",
    "GCP_PROJECT_ID",
    "ANTHROPIC_API_KEY",
    "API_SECRET",
    "IMAGE_CF_URL",
    "GENERATE_VIDEO_CF_URL",
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


@app.get("/_debug/deps")
def _debug_deps() -> dict:
    """Confirm what packages are actually installed in the running container."""
    import importlib.util
    import sys
    return {
        "python": sys.version,
        "anthropic": importlib.util.find_spec("anthropic") is not None,
        "replicate": importlib.util.find_spec("replicate") is not None,
        "google_genai": importlib.util.find_spec("google.genai") is not None,
    }


@app.post("/_debug/discover")
def _debug_discover() -> dict:
    """Trigger discovery inside the live container, return result."""
    from execution.viral_finder import discover_and_email
    result = discover_and_email()
    return {"emails_sent": result}
