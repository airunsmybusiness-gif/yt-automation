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

    scheduler.add_job(
        _discover_daily,
        "cron",
        day_of_week="tue,thu,sat",
        hour=14,
        minute=0,
        id="discover_daily",
        max_instances=1,
        coalesce=True,
    )
    log.info("Registered job: discover_daily Tue/Thu/Sat at 14:00 UTC (8am CST)")

    scheduler.add_job(
        _poll_approvals,
        "interval",
        seconds=60,
        id="poll_approvals",
        max_instances=1,
        coalesce=True,
    )
    log.info("Registered job: poll_approvals every 60s")


async def _process_next(pipeline: Pipeline) -> None:
    try:
        pipeline.process_next()
    except Exception as exc:
        log.exception(f"process_next error: {exc}")


async def _discover_daily() -> None:
    try:
        from execution.viral_finder import discover_and_email
        sent = discover_and_email()
        log.info(f"discover_daily: emails sent = {sent}")
    except Exception as exc:
        log.exception(f"discover_daily error: {exc}")


async def _poll_approvals() -> None:
    try:
        from execution.email_approval_poller import poll_approvals
        updated = poll_approvals()
        if updated:
            log.info(f"poll_approvals: {updated} approval(s) processed")
    except Exception as exc:
        log.exception(f"poll_approvals error: {exc}")
