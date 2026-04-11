from __future__ import annotations
import logging, os, re, time
from typing import Any
from openai import OpenAI
from supabase import create_client, Client

logger = logging.getLogger(__name__)
SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_KEY"]
OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]

AGENT_MODELS: dict[str, str] = {
    "agent1_analyzer":      "gpt-4o",
    "agent2_strategist":    "gpt-4o",
    "agent3_script_writer": "gpt-4o",
    "agent4_optimizer":     "gpt-4o",
}

_client: OpenAI | None = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client

def _get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def _load_prompt(agent_name: str) -> str:
    sb = _get_supabase()
    rows = sb.table("yt_agent_prompts").select("prompt_content").eq("agent_name", agent_name).eq("is_active", True).execute()
    if not rows.data:
        raise ValueError(f"No prompt for '{agent_name}'")
    return rows.data[0]["prompt_content"]

def _call_openai(agent_name: str, user_message: str, max_retries: int = 3) -> str:
    model = AGENT_MODELS[agent_name]
    system_prompt = _load_prompt(agent_name)
    client = _get_client()
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}],
                max_tokens=8192,
                temperature=0.7,
            )
            text = response.choices[0].message.content
            if not text:
                raise ValueError("Empty response")
            logger.info("agent=%s attempt=%d chars=%d", agent_name, attempt, len(text))
            return text
        except Exception as exc:
            logger.warning("agent=%s attempt=%d error=%s", agent_name, attempt, exc)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"exhausted retries for {agent_name}")

def run_agent(agent_name: str, user_message: str) -> str:
    logger.info("Running agent: %s", agent_name)
    return _call_openai(agent_name, user_message)

def run_agent1_analyzer(supabase_client: Any, video: dict, transcript: str, comments: list) -> str:
    return run_agent("agent1_analyzer", f"Transcript: {transcript}\nComments: {str(comments[:20])}\nTitle: {video.get('title','')}")

def run_agent2_strategist(supabase_client: Any, video: dict, analyzer_result: str) -> str:
    return run_agent("agent2_strategist", f"Viral analysis:\n{analyzer_result}\n\nTitle: {video.get('title','')}")

def run_agent3_script_writer(supabase_client: Any, video: dict, analyzer_result: str, strategist_result: str) -> str:
    return run_agent("agent3_script_writer", f"Strategy:\n{strategist_result}\n\nAnalysis:\n{analyzer_result}\n\nTitle: {video.get('title','')}")

def run_agent4_optimizer(supabase_client: Any, video: dict, script: str) -> str:
    return run_agent("agent4_optimizer", f"Script:\n{script}\n\nTitle: {video.get('title','')}")

def save_script_to_db(supabase_client: Any, viral_video_id: str, script_text: str) -> int:
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', script_text) if s.strip()]
    for i, sentence in enumerate(sentences, start=1):
        supabase_client.table("yt_scripts").upsert({"viral_video_id": viral_video_id, "sentence_number": i, "sentence_text": sentence}, on_conflict="viral_video_id, sentence_number").execute()
    logger.info("Saved %d sentences for %s", len(sentences), viral_video_id)
    return len(sentences)

def run_pipeline(viral_video_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    results["agent1_analyzer"] = run_agent("agent1_analyzer", f"Transcript: {input_data.get('transcript','')}\nTitle: {input_data.get('title','')}")
    results["agent2_strategist"] = run_agent("agent2_strategist", f"Analysis:\n{results['agent1_analyzer']}\nTitle: {input_data.get('title','')}")
    results["agent3_script_writer"] = run_agent("agent3_script_writer", f"Strategy:\n{results['agent2_strategist']}\nTranscript:\n{input_data.get('transcript','')}")
    results["agent4_optimizer"] = run_agent("agent4_optimizer", f"Script:\n{results['agent3_script_writer']}")
    return results
