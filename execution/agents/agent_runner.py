"""
execution/agents/agent_runner.py
DOE Execution layer — Gemini via Vertex AI REST API (same pattern as tts_service.py).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import google.auth
import google.auth.transport.requests
import requests
from supabase import create_client, Client

logger = logging.getLogger(__name__)

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
GCP_PROJECT: str = os.environ.get("GCP_PROJECT_ID", "youtube-automation-492419")
GCP_LOCATION: str = "us-central1"

AGENT_MODELS: dict[str, str] = {
    "agent1_analyzer":      "claude-sonnet-4-5@20251001",
    "agent2_strategist":    "claude-sonnet-4-5@20251001",
    "agent3_script_writer": "claude-sonnet-4-5@20251001",
    "agent4_optimizer":     "claude-sonnet-4-5@20251001",
}


def _get_token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _load_prompt(agent_name: str) -> str:
    sb = _get_supabase()
    rows = (
        sb.table("yt_agent_prompts")
        .select("prompt_content")
        .eq("agent_name", agent_name)
        .eq("is_active", True)
        .execute()
    )
    if not rows.data:
        raise ValueError(f"No prompt found for agent '{agent_name}'")
    return rows.data[0]["prompt_content"]


def _call_gemini(agent_name: str, user_message: str, max_retries: int = 3) -> str:
    model = AGENT_MODELS[agent_name]
    system_prompt = _load_prompt(agent_name)
    url = (
        f"https://{GCP_LOCATION}-aiplatform.googleapis.com/v1/projects/{GCP_PROJECT}"
        f"/locations/{GCP_LOCATION}/publishers/google/models/{model}:generateContent"
    )

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8192},
    }

    for attempt in range(1, max_retries + 1):
        try:
            token = _get_token()
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            if not text:
                raise ValueError("Empty response")
            logger.info("agent=%s attempt=%d chars=%d", agent_name, attempt, len(text))
            return text
        except Exception as exc:
            logger.warning("agent=%s attempt=%d error=%s", agent_name, attempt, exc)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)

    raise RuntimeError(f"_call_gemini exhausted retries for {agent_name}")


def run_agent(agent_name: str, user_message: str) -> str:
    if agent_name not in AGENT_MODELS:
        raise ValueError(f"Unknown agent '{agent_name}'")
    logger.info("Running agent: %s", agent_name)
    return _call_gemini(agent_name, user_message)


def run_agent1_analyzer(supabase_client: Any, video: dict, transcript: str, comments: list) -> str:
    return run_agent("agent1_analyzer",
        f"Transcript: {transcript}\nComments sample: {str(comments[:20])}\nVideo title: {video.get('title', '')}")

def run_agent2_strategist(supabase_client: Any, video: dict, analyzer_result: str) -> str:
    return run_agent("agent2_strategist",
        f"Viral analysis:\n{analyzer_result}\n\nOriginal title: {video.get('title', '')}")

def run_agent3_script_writer(supabase_client: Any, video: dict, analyzer_result: str, strategist_result: str) -> str:
    return run_agent("agent3_script_writer",
        f"Strategy:\n{strategist_result}\n\nViral analysis:\n{analyzer_result}\n\nVideo title: {video.get('title', '')}")

def run_agent4_optimizer(supabase_client: Any, video: dict, script: str) -> str:
    return run_agent("agent4_optimizer",
        f"Script:\n{script}\n\nVideo title: {video.get('title', '')}")

def save_script_to_db(supabase_client: Any, viral_video_id: str, script_text: str) -> int:
    import re
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', script_text) if s.strip()]
    for i, sentence in enumerate(sentences, start=1):
        supabase_client.table("yt_scripts").upsert({
            "viral_video_id": viral_video_id,
            "sentence_number": i,
            "sentence_text": sentence,
        }, on_conflict="viral_video_id,sentence_number").execute()
    logger.info("Saved %d sentences for %s", len(sentences), viral_video_id)
    return len(sentences)

def run_pipeline(viral_video_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    results["agent1_analyzer"] = run_agent("agent1_analyzer",
        f"Transcript: {input_data.get('transcript', '')}\nComments: {input_data.get('comments', '')}\nTitle: {input_data.get('title', '')}")
    results["agent2_strategist"] = run_agent("agent2_strategist",
        f"Viral analysis:\n{results['agent1_analyzer']}\n\nTitle: {input_data.get('title', '')}")
    results["agent3_script_writer"] = run_agent("agent3_script_writer",
        f"Strategy:\n{results['agent2_strategist']}\n\nTranscript:\n{input_data.get('transcript', '')}")
    results["agent4_optimizer"] = run_agent("agent4_optimizer",
        f"Script:\n{results['agent3_script_writer']}\n\nStrategy:\n{results['agent2_strategist']}")
    return results
