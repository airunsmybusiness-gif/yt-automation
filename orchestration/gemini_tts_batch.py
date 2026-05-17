"""
Gemini TTS via Batch API.

Flow:
  1. Build JSONL with one TTS request per sentence
  2. Upload JSONL to Gemini Files API
  3. Submit batch job (model: gemini-2.5-flash-preview-tts)
  4. Poll until done (or timeout)
  5. Return output file resource name → pass to upload-audio-to-gcs CF

Output JSONL format (per line):
  {"key": "<sentence_number>", "response": {"candidates": [{"content":
    {"parts": [{"inlineData": {"mimeType": "audio/L16;rate=24000",
      "data": "<base64>"}}]}}]}}
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com"
TTS_MODEL = "models/gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"
POLL_INTERVAL_SEC = 30
MAX_WAIT_SEC = 20 * 60  # 20-min cap for TTS (cheaper, faster than Imagen)

TERMINAL_STATES = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED"}


def _headers(api_key: str) -> dict[str, str]:
    return {"x-goog-api-key": api_key, "Content-Type": "application/json"}


def _upload_jsonl_to_files_api(api_key: str, jsonl_bytes: bytes) -> str:
    """Upload JSONL to Gemini Files API, return 'files/{id}'."""
    # Step 1: initiate resumable upload
    init_url = (
        f"{GEMINI_BASE}/upload/v1beta/files"
        "?uploadType=resumable"
    )
    init_resp = requests.post(
        init_url,
        headers={
            "x-goog-api-key": api_key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(jsonl_bytes)),
            "X-Goog-Upload-Header-Content-Type": "application/jsonlines",
            "Content-Type": "application/json",
        },
        json={"file": {"mimeType": "application/jsonlines"}},
        timeout=30,
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError(
            f"No upload URL from Files API init: {init_resp.headers}"
        )

    # Step 2: upload bytes
    up_resp = requests.post(
        upload_url,
        headers={
            "Content-Length": str(len(jsonl_bytes)),
            "X-Goog-Upload-Command": "upload, finalize",
            "X-Goog-Upload-Offset": "0",
        },
        data=jsonl_bytes,
        timeout=120,
    )
    up_resp.raise_for_status()
    file_meta = up_resp.json()
    file_name = file_meta.get("file", {}).get("name") or file_meta.get("name")
    if not file_name:
        raise RuntimeError(f"No file name in upload response: {file_meta}")
    log.info(f"[TTS] Uploaded input JSONL to Files API: {file_name}")
    return file_name


def _submit_batch(api_key: str, input_file_uri: str) -> str:
    """Submit Gemini batch job, return batch name like 'batches/{id}'."""
    url = f"{GEMINI_BASE}/v1beta/batches"
    payload: dict[str, Any] = {
        "model": TTS_MODEL,
        "input_source": {
            "file_data": {
                "mime_type": "application/jsonlines",
                "file_uri": input_file_uri,
            }
        },
    }
    resp = requests.post(url, headers=_headers(api_key), json=payload, timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Gemini batch submit failed {resp.status_code}: {resp.text[:400]}"
        )
    batch_name = resp.json().get("name")
    if not batch_name:
        raise RuntimeError(f"No batch name in response: {resp.json()}")
    log.info(f"[TTS] Batch submitted: {batch_name}")
    return batch_name


def _poll_batch(api_key: str, batch_name: str) -> dict[str, Any]:
    """Poll until terminal state, raise on failure/timeout."""
    url = f"{GEMINI_BASE}/v1beta/{batch_name}"
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed > MAX_WAIT_SEC:
            raise RuntimeError(
                f"[TTS] Batch {batch_name} timed out after {elapsed/60:.1f} min"
            )
        resp = requests.get(url, headers=_headers(api_key), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        state = data.get("state", "")
        log.info(f"[TTS] Batch {batch_name} state={state} elapsed={elapsed/60:.1f}min")
        if state in TERMINAL_STATES:
            return data
        time.sleep(POLL_INTERVAL_SEC)


def submit_tts_batch(
    sentences: list[dict],
    api_key: str,
) -> str:
    """
    Submit a Gemini TTS batch for all sentences.

    Args:
        sentences: list of {"sentence_number": int, "sentence_text": str}
        api_key:   Gemini API key

    Returns:
        output_file_uri: e.g. "files/abc123" — pass to upload-audio-to-gcs CF
    """
    lines = []
    for s in sentences:
        entry = {
            "key": str(s["sentence_number"]),
            "request": {
                "contents": [{"parts": [{"text": s["sentence_text"]}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": TTS_VOICE}
                        }
                    },
                },
            },
        }
        lines.append(json.dumps(entry))

    jsonl_bytes = "\n".join(lines).encode("utf-8")
    log.info(f"[TTS] Submitting batch for {len(sentences)} sentences")

    input_file_uri = _upload_jsonl_to_files_api(api_key, jsonl_bytes)
    batch_name = _submit_batch(api_key, input_file_uri)
    result = _poll_batch(api_key, batch_name)

    state = result.get("state")
    if state != "JOB_STATE_SUCCEEDED":
        raise RuntimeError(
            f"[TTS] Batch ended with state={state}: {json.dumps(result)[:400]}"
        )

    output_uri = (
        result.get("output_source", {})
        .get("file_data", {})
        .get("file_uri")
    )
    if not output_uri:
        raise RuntimeError(f"[TTS] No output_source.file_data.file_uri in result: {result}")

    log.info(f"[TTS] Batch complete. Output: {output_uri}")
    return output_uri
