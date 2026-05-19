import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from orchestration.pipeline import Pipeline
from orchestration.discovery import discover_and_queue

log = logging.getLogger("scheduler")


def register_jobs(scheduler: AsyncIOScheduler, pipeline: Pipeline) -> None:
    scheduler.add_job(
        _process_next,
        "interval",
        minutes=2,
        id="process_next",
        args=[pipeline],
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _discover,
        "cron",
        hour=9,
        minute=0,
        id="discover_daily",
        max_instances=1,
        coalesce=True,
    )
    log.info("Registered jobs: process_next every 2 min, discover_daily at 09:00 UTC")


async def _process_next(pipeline: Pipeline) -> None:
    try:
        pipeline.process_next()
    except Exception as exc:
        log.exception(f"process_next error: {exc}")


async def _discover() -> None:
    try:
        count = discover_and_queue()
        log.info(f"Discovery complete: {count} videos queued")
    except Exception as exc:
        log.exception(f"discover error: {exc}")
