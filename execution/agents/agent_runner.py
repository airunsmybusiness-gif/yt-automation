"""Agent runner — sequential 4-agent chain via Anthropic SDK."""

import json
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

MODEL_HEAVY = "claude-sonnet-4-20250514"  # agents 1-3
MODEL_LIGHT = "claude-haiku-4-5-20251001"  # agent 4 (optimizer) + scene transforms


def _call_agent(
    client: anthropic.Anthropic,
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int = 8192,
) -> str:
    """Call a single agent and return its text response."""
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def _load_prompt(supabase_client: Any, agent_name: str) -> str:
    """Load active prompt from yt_agent_prompts."""
    resp = (
        supabase_client.table("yt_agent_prompts")
        .select("prompt_content")
        .eq("agent_name", agent_name)
        .eq("is_active", True)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise ValueError(f"No active prompt found for agent: {agent_name}")
    return resp.data[0]["prompt_content"]


def run_agent_pipeline(
    supabase_client: Any,
    anthropic_api_key: str,
    video: dict[str, Any],
    transcript: str,
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run all 4 agents sequentially. Returns final script sentences.

    Args:
        supabase_client: Supabase client.
        anthropic_api_key: Anthropic API key.
        video: yt_viral_videos record.
        transcript: Source transcript text.
        comments: List of comment dicts.

    Returns:
        Dict with analyzer_result, strategist_result, sentences (final script).
    """
    client = anthropic.Anthropic(api_key=anthropic_api_key)
    record_id = video["id"]

    # Format comments for agent input
    comments_text = "\n".join(
        f"- {c.get('content', '')[:200]}" for c in comments[:50]
    )
    thumbnail_desc = video.get("thumbnail_description", "No description available")

    # --- Agent 1: Viral Analyzer ---
    logger.info("Running Agent 1: Viral Analyzer")
    analyzer_prompt = _load_prompt(supabase_client, "agent1_analyzer")
    analyzer_input = (
        f"VIDEO TITLE: {video.get('title', 'Unknown')}\n"
        f"VIEWS: {video.get('views', 0)}\n"
        f"THUMBNAIL DESCRIPTION: {thumbnail_desc}\n\n"
        f"TRANSCRIPT:\n{transcript[:15000]}\n\n"
        f"TOP COMMENTS:\n{comments_text}"
    )
    analyzer_raw = _call_agent(client, analyzer_prompt, analyzer_input, MODEL_HEAVY)

    # Save to DB
    supabase_client.table("yt_viral_analyzer_results").upsert({
        "video_record_id": record_id,
        "video_id": video["video_id"],
        "human_readable_summary": {"raw_output": analyzer_raw},
    }).execute()

    # --- Agent 2: Strategist ---
    logger.info("Running Agent 2: Strategist")
    strategist_prompt = _load_prompt(supabase_client, "agent2_strategist")
    strategist_input = (
        f"VIRAL ANALYSIS:\n{analyzer_raw}\n\n"
        f"ORIGINAL VIDEO TITLE: {video.get('title', 'Unknown')}\n"
        f"ORIGINAL DESCRIPTION: {video.get('description', '')[:2000]}"
    )
    strategist_raw = _call_agent(client, strategist_prompt, strategist_input, MODEL_HEAVY)

    # Save to DB
    supabase_client.table("yt_strategist_results").upsert({
        "video_record_id": record_id,
        "video_id": video["video_id"],
        "strategy_brief": {"raw_output": strategist_raw},
    }).execute()

    # --- Agent 3: Script Writer ---
    logger.info("Running Agent 3: Script Writer")
    script_prompt = _load_prompt(supabase_client, "agent3_script_writer")
    script_input = (
        f"VIRAL ANALYSIS:\n{analyzer_raw}\n\n"
        f"STRATEGY BRIEF:\n{strategist_raw}\n\n"
        f"SOURCE TRANSCRIPT:\n{transcript[:10000]}"
    )
    script_raw = _call_agent(client, script_prompt, script_input, MODEL_HEAVY)

    # --- Agent 4: Optimizer ---
    logger.info("Running Agent 4: Optimizer")
    optimizer_prompt = _load_prompt(supabase_client, "agent4_optimizer")
    optimized_raw = _call_agent(client, optimizer_prompt, script_raw, MODEL_LIGHT)

    # Parse sentences (expect numbered lines or JSON array)
    sentences = _parse_sentences(optimized_raw)
    logger.info("Agent pipeline complete: %d sentences", len(sentences))

    # Save sentences to yt_scripts
    for sent in sentences:
        supabase_client.table("yt_scripts").insert({
            "viral_video_id": record_id,
            "sentence_number": sent["sentence_number"],
            "sentence_text": sent["sentence_text"],
            "section": sent.get("section", "body"),
        }).execute()

    return {
        "analyzer_result": analyzer_raw,
        "strategist_result": strategist_raw,
        "sentences": sentences,
    }


def transform_scene(
    client: anthropic.Anthropic,
    sentence_texts: list[str],
) -> str:
    """Transform script sentences into a visual scene description for image gen.

    Args:
        client: Anthropic client.
        sentence_texts: 2-3 sentences to visualize.

    Returns:
        Scene description string for image prompt.
    """
    combined = " ".join(sentence_texts)
    prompt = (
        "Transform this script excerpt into a single visual scene description "
        "for an AI image generator. Describe what the viewer should SEE — "
        "not the words being spoken. Be specific about composition, lighting, "
        "mood, and visual elements. One paragraph, under 100 words.\n\n"
        f"SCRIPT: {combined}"
    )
    return _call_agent(
        client,
        "You are a visual director for a psychology YouTube channel.",
        prompt,
        MODEL_LIGHT,
        max_tokens=200,
    )


def _parse_sentences(raw_output: str) -> list[dict[str, Any]]:
    """Parse agent output into numbered sentences."""
    # Try JSON first
    try:
        data = json.loads(raw_output)
        if isinstance(data, list):
            return [
                {
                    "sentence_number": i + 1,
                    "sentence_text": (
                        item.get("sentence_text") or item.get("text") or str(item)
                    ),
                    "section": item.get("section", "body"),
                }
                for i, item in enumerate(data)
            ]
    except (json.JSONDecodeError, TypeError):
        pass

    # Fall back to line parsing
    sentences = []
    for line in raw_output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Remove leading numbers: "1. Text" or "1) Text" or "1: Text"
        for sep in [". ", ") ", ": "]:
            if line[0].isdigit() and sep in line:
                idx = line.index(sep)
                line = line[idx + len(sep):]
                break
        if len(line) > 5:  # skip tiny fragments
            sentences.append({
                "sentence_number": len(sentences) + 1,
                "sentence_text": line,
                "section": "body",
            })

    return sentences
