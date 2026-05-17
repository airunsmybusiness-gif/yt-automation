"""
GCP credentials helper for Railway. Supports two auth methods:
  1. GCP_SERVICE_ACCOUNT_JSON env var (JSON string of service account key)
  2. GOOGLE_APPLICATION_CREDENTIALS env var (path to key file)
"""
from __future__ import annotations

import json
import logging
import os
import tempfile

import google.auth
import google.auth.transport.requests

log = logging.getLogger(__name__)

_creds = None
_creds_project: str | None = None
_sa_tmp_path: str | None = None


def _bootstrap_credentials() -> None:
    global _sa_tmp_path
    sa_json_str = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if sa_json_str and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            json.loads(sa_json_str)  # validate
        except json.JSONDecodeError as exc:
            raise ValueError("GCP_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".json", mode="w", prefix="gcp_sa_"
        )
        tmp.write(sa_json_str)
        tmp.close()
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name
        _sa_tmp_path = tmp.name
        log.info("Wrote GCP_SERVICE_ACCOUNT_JSON to temp file for ADC")


def get_access_token() -> str:
    _bootstrap_credentials()
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token
