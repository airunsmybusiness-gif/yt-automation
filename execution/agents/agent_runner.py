"""
execution/agents/agent_runner.py
DOE Execution layer — Gemini via Vertex AI (uses GCP service account, not AI Studio key).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
from supabase import create_client, Client

logger = logging.getLogger(__name__)

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
GCP_PROJECT: str = os.environ.get("GCP_PROJECT_ID", "youtube-automation-492419")
GCP_LOCATION: str = "us-central1"

AGENT_MODELS: dict[str, str] = {
    "agent1_analyzer":      "gemini-3.1-pro-preview",
    "agent2_strategist":    "gemini-3.1-pro-preview",
    "agent3_script_writer": "gemini-3.1-pro-preview",
    "agent4_optimizer":     "gemini-3.1-pro-preview",
}

_vertex_initialized = False


def _init_vertex() -> None:
    global _vertex_initialized
    if not _vertex_initialized:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        _vertex_initialized = True


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
    _init_vertex()
    model_id = AGENT_MODELS[agent_name]
    system_prompt = _load_prompt(agent_name)

    model = GenerativeModel(
        model_name=model_id,
        system_instruction=system_prompt,
    )
    config = GenerationConfig(temperature=0.7, max_output_tokens=8192)

    for attempt in range(1, max_retries + 1):
        try:
            response = model.generate_content(user_message, generation_config=config)
            text = response.text
            if not text:
                raise ValueError("Empty response from Gemini")
            logger.info("agent=%s model=%s attempt=%d chars=%d", agent_name, model_id, attempt, len(text))
            return text
        except Exception as exc:
            logger.warning("agent=%s attempt=%d error=%s", agent_name, attempt, exc)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)

    raise RuntimeError(f"_call_gemini exhausted retries for {agent_name}")


def run_agent(agent_name: str, user_message: str) -> str:
    if agent_name not in AGENT_MODELS:
        raise ValueError(f"Unknown agent '{agent_name}'. Valid: {list(AGENT_MODELS)}")
    logger.info("Running agent: %s", agent_name)
    return _call_gemini(agent_name, user_message)


def run_pipeline(viral_video_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    results["agent1_analyzer"] = run_agent("agent1_analyzer",
        f"Transcript: {input_data.get('transcript', '')}\n"
        f"Comments sample: {input_data.get('comments', '')}\n"
        f"Video title: {input_data.get('title', '')}"
    )
    results["agent2_strategist"] = run_agent("agent2_strategist",
        f"Viral analysis:\n{results['agent1_analyzer']}\n\n"
        f"Original title: {input_data.get('title', '')}"
    )
    results["agent3_script_writer"] = run_agent("agent3_script_writer",
        f"Strategy:\n{results['agent2_strategist']}\n\n"
        f"Transcript:\n{input_data.get('transcript', '')}"
    )
    results["agent4_optimizer"] = run_agent("agent4_optimizer",
        f"Script:\n{results['agent3_script_writer']}\n\n"
        f"Strategy:\n{results['agent2_strategist']}"
    )
    logger.info("Pipeline complete for viral_video_id=%s", viral_video_id)
    return results


def run_agent1_analyzer(supabase_client: Any, video: dict, transcript: str, comments: list) -> str:
    return run_agent("agent1_analyzer",
        f"Transcript: {transcript}\n"
        f"Comments sample: {str(comments[:20])}\n"
        f"Video title: {video.get('title', '')}"
    )

def run_agent2_strategist(supabase_client: Any, video: dict, analyzer_result: str) -> str:
    return run_agent("agent2_strategist",
        f"Viral analysis:\n{analyzer_result}\n\n"
        f"Original title: {video.get('title', '')}"
    )

def run_agent3_script_writer(supabase_client: Any, video: dict, analyzer_result: str, strategist_result: str) -> str:
    return run_agent("agent3_script_writer",
        f"Strategy:\n{strategist_result}\n\n"
        f"Viral analysis:\n{analyzer_result}\n\n"
        f"Video title: {video.get('title', '')}"
    )

def run_agent4_optimizer(supabase_client: Any, video: dict, script: str) -> str:
    return run_agent("agent4_optimizer",
        f"Script:\n{script}\n\n"
        f"Video title: {video.get('title', '')}"
    )

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
