"""
execution/agents/agent_runner.py
DOE Execution layer — runs 4-agent pipeline via Gemini 2.5.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from google import genai
from google.genai import types
from supabase import create_client, Client

logger = logging.getLogger(__name__)

GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]

AGENT_MODELS: dict[str, str] = {
    "viral_analyzer":  "gemini-2.5-flash-preview-04-17",
    "strategist":      "gemini-2.5-flash-preview-04-17",
    "script_writer":   "gemini-2.5-pro-preview-03-25",
    "optimizer":       "gemini-2.5-flash-preview-04-17",
}

_gemini_client: genai.Client | None = None


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _load_prompt(agent_name: str) -> str:
    sb = _get_supabase()
    row = (
        sb.table("yt_agent_prompts")
        .select("prompt_text")
        .eq("agent_name", agent_name)
        .single()
        .execute()
    )
    if not row.data:
        raise ValueError(f"No prompt found for agent '{agent_name}'")
    return row.data["prompt_text"]


def _call_gemini(agent_name: str, user_message: str, max_retries: int = 3) -> str:
    model_id = AGENT_MODELS[agent_name]
    system_prompt = _load_prompt(agent_name)
    client = _get_gemini_client()

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.7,
                    max_output_tokens=8192,
                ),
            )
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

    results["viral_analyzer"] = run_agent("viral_analyzer",
        f"Transcript: {input_data.get('transcript', '')}\n"
        f"Comments sample: {input_data.get('comments', '')}\n"
        f"Competitor thumbnail: {input_data.get('thumbnail_url', '')}"
    )
    results["strategist"] = run_agent("strategist",
        f"Viral analysis:\n{results['viral_analyzer']}\n\n"
        f"Original title: {input_data.get('title', '')}"
    )
    results["script_writer"] = run_agent("script_writer",
        f"Strategy:\n{results['strategist']}\n\n"
        f"Transcript for research:\n{input_data.get('transcript', '')}"
    )
    results["optimizer"] = run_agent("optimizer",
        f"Script:\n{results['script_writer']}\n\n"
        f"Strategy:\n{results['strategist']}"
    )

    logger.info("Pipeline complete for viral_video_id=%s", viral_video_id)
    return results


# Legacy aliases for orchestration/pipeline.py compatibility
def run_agent1_analyzer(input_data: dict) -> str:
    return run_agent("viral_analyzer", 
        f"Transcript: {input_data.get('transcript', '')}\n"
        f"Comments sample: {input_data.get('comments', '')}\n"
        f"Competitor thumbnail: {input_data.get('thumbnail_url', '')}"
    )

def run_agent2_strategist(input_data: dict) -> str:
    return run_agent("strategist",
        f"Viral analysis:\n{input_data.get('analyzer_result', '')}\n\n"
        f"Original title: {input_data.get('title', '')}"
    )

def run_agent3_script_writer(input_data: dict) -> str:
    return run_agent("script_writer",
        f"Strategy:\n{input_data.get('strategist_result', '')}\n\n"
        f"Transcript for research:\n{input_data.get('transcript', '')}"
    )

def run_agent4_optimizer(input_data: dict) -> str:
    return run_agent("optimizer",
        f"Script:\n{input_data.get('script', '')}\n\n"
        f"Strategy:\n{input_data.get('strategist_result', '')}"
    )


def save_script_to_db(supabase_client: Any, viral_video_id: str, script_text: str) -> None:
    """Parse script into sentences and save to yt_scripts table."""
    import re
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', script_text) if s.strip()]
    for i, sentence in enumerate(sentences, start=1):
        supabase_client.table("yt_scripts").upsert({
            "viral_video_id": viral_video_id,
            "sentence_number": i,
            "sentence_text": sentence,
        }, on_conflict="viral_video_id,sentence_number").execute()
    logger.info("Saved %d sentences to yt_scripts for %s", len(sentences), viral_video_id)
