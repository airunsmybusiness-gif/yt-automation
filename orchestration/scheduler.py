import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from orchestration.pipeline import Pipeline

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
    log.info("Registered job: process_next every 2 minutes")


async def _process_next(pipeline: Pipeline) -> None:
    try:
        pipeline.process_next()
    except Exception as exc:
        log.exception(f"process_next error: {exc}")
