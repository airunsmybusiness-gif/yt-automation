"""
Vertex AI batch job polling with hard 30-minute timeout + auto-cancel.

CRITICAL: Prevents runaway billing ($124 incident from spinning jobs).
Every call to poll_until_done() will cancel the job if it exceeds MAX_WAIT_SEC.
"""
from __future__ import annotations

import logging
import time

import requests

from orchestration.gcp_auth import get_access_token

log = logging.getLogger(__name__)

MAX_WAIT_SEC = 30 * 60  # hard 30-minute kill
POLL_INTERVAL_SEC = 60  # check every minute

TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_PAUSED",
}
SUCCESS_STATE = "JOB_STATE_SUCCEEDED"


def _endpoint_base(job_name: str, location: str) -> str:
    if location == "global":
        return "https://aiplatform.googleapis.com"
    return f"https://{location}-aiplatform.googleapis.com"


def _get_job_status(job_name: str, location: str) -> dict:
    token = get_access_token()
    base = _endpoint_base(job_name, location)
    url = f"{base}/v1/{job_name}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _cancel_job(job_name: str, location: str) -> None:
    try:
        token = get_access_token()
        base = _endpoint_base(job_name, location)
        url = f"{base}/v1/{job_name}:cancel"
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        log.warning(
            f"[VertexGuard] CANCEL sent for {job_name}: HTTP {resp.status_code}"
        )
    except Exception as exc:
        log.error(f"[VertexGuard] Failed to cancel {job_name}: {exc}")


def poll_until_done(job_name: str, location: str = "us-central1") -> dict:
    """
    Poll a Vertex AI batch job until it completes or 30 minutes elapse.

    If the job exceeds 30 minutes it is automatically cancelled via API and
    a RuntimeError is raised — preventing any further charges.

    Returns the final job status dict on success.
    Raises RuntimeError on failure, cancellation, or timeout.
    """
    start = time.monotonic()
    log.info(f"[VertexGuard] Polling {job_name} (max {MAX_WAIT_SEC//60} min)")

    while True:
        elapsed = time.monotonic() - start

        if elapsed > MAX_WAIT_SEC:
            log.error(
                f"[VertexGuard] {job_name} exceeded {MAX_WAIT_SEC//60} min — "
                "sending cancel to prevent billing overrun"
            )
            _cancel_job(job_name, location)
            raise RuntimeError(
                f"Vertex AI batch job cancelled after {elapsed/60:.1f} min "
                f"(hard limit {MAX_WAIT_SEC//60} min): {job_name}"
            )

        try:
            status = _get_job_status(job_name, location)
        except Exception as exc:
            log.warning(f"[VertexGuard] Status check failed ({exc}), retrying")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        state = status.get("state", "UNKNOWN")
        display = status.get("displayName", job_name)
        log.info(
            f"[VertexGuard] {display} state={state} "
            f"elapsed={elapsed/60:.1f}min / {MAX_WAIT_SEC//60}min"
        )

        if state in TERMINAL_STATES:
            if state == SUCCESS_STATE:
                return status
            raise RuntimeError(
                f"Vertex AI batch job ended with state={state}: {job_name}"
            )

        time.sleep(POLL_INTERVAL_SEC)
