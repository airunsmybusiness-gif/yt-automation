"""Agent runner — sequential Claude API calls for the 4-agent pipeline.

Agent 1 (Analyzer): video + transcript + comments → analysis JSON
Agent 2 (Strategist): Agent 1 output → strategy JSON
Agent 3 (Script Writer): Agent 1 + Agent 2 → sentence array
Agent 4 (Optimizer): Agent 3 → cleaned sentence array

All prompts loaded from yt_agent_prompts table.
All agents return strict JSON only.
"""

import json
import logging
import os
from typing import Any

import anthropic

from execution.utils.exceptions import AgentPipelineError

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-opus-4-6"
MAX_TOKENS = 32000
AGENT_TIMEOUT = 300  # seconds


def _get_claude_client() -> anthropic.Anthropic:
    """Create Claude API client."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AgentPipelineError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=api_key)


def _load_prompt(supabase_client: Any, agent_name: str) -> str:
    """Load agent prompt from yt_agent_prompts table.

    Args:
        supabase_client: Supabase client.
        agent_name: One of: agent1_analyzer, agent2_strategist,
                    agent3_script_writer, agent4_optimizer.

    Returns:
        The prompt_content string.

    Raises:
        AgentPipelineError: If prompt not found.
    """
    resp = (
        supabase_client.table("yt_agent_prompts")
        .select("prompt_content")
        .eq("agent_name", agent_name)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise AgentPipelineError(f"Agent prompt '{agent_name}' not found in DB")
    return resp.data[0]["prompt_content"]


def _call_claude(
    client: anthropic.Anthropic,
    system_prompt: str,
    user_message: str,
    retry_on_json_error: bool = True,
) -> dict | list:
    """Call Claude API and parse JSON response.

    Args:
        client: Anthropic client.
        system_prompt: System prompt from yt_agent_prompts.
        user_message: The user message with data payload.
        retry_on_json_error: If True, retry once with stricter prompt on parse failure.

    Returns:
        Parsed JSON (dict or list).

    Raises:
        AgentPipelineError: On persistent failures.
    """
    for attempt in range(2 if retry_on_json_error else 1):
        try:
            extra_instruction = ""
            if attempt > 0:
                extra_instruction = (
                    "\n\nCRITICAL: Your previous response was not valid JSON. "
                    "Respond ONLY with valid JSON. No markdown, no code fences, "
                    "no explanation. Start with { or [."
                )

            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt + extra_instruction,
                messages=[{"role": "user", "content": user_message}],
            )

            raw_text = message.content[0].text.strip()
            return _parse_json_response(raw_text)

        except json.JSONDecodeError as e:
            logger.warning(
                "ANNEALING: Agent returned invalid JSON (attempt %d): %s",
                attempt + 1, str(e)[:200],
            )
            if attempt == 0 and retry_on_json_error:
                continue
            raise AgentPipelineError(
                f"Agent returned invalid JSON after retry: {str(e)[:200]}"
            ) from e

        except anthropic.APIError as e:
            logger.error("Claude API error: %s", e)
            raise AgentPipelineError(f"Claude API error: {e}") from e

    raise AgentPipelineError("Unreachable")  # pragma: no cover


def _parse_json_response(text: str) -> dict | list:
    """Parse JSON from Claude response, stripping code fences if present."""
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines if they're fences
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    return json.loads(text)


# ---------------------------------------------------------------------------
# Agent 1: Viral Analyzer
# ---------------------------------------------------------------------------

def run_agent1_analyzer(
    supabase_client: Any,
    video: dict[str, Any],
    transcript: str,
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run Agent 1 — Viral Analyzer.

    Args:
        supabase_client: Supabase client.
        video: yt_viral_videos record.
        transcript: Full transcript text.
        comments: List of comment records.

    Returns:
        Analysis JSON saved to yt_viral_analyzer_results.
    """
    logger.info("Running Agent 1 (Analyzer) for %s", video["video_id"])
    client = _get_claude_client()
    prompt = _load_prompt(supabase_client, "agent1_analyzer")

    # Format top comments for context (limit to top 50 by likes)
    sorted_comments = sorted(
        comments, key=lambda c: c.get("like_count", 0), reverse=True
    )[:50]
    comments_text = "\n".join(
        f"[{c.get('like_count', 0)} likes] {c.get('content', '')[:300]}"
        for c in sorted_comments
    )

    user_msg = (
        f"VIDEO METADATA:\n"
        f"Title: {video.get('title', '')}\n"
        f"Channel: {video.get('channel_title', '')}\n"
        f"Views: {video.get('views', 0)}\n"
        f"Likes: {video.get('likes', 0)}\n"
        f"Comments Count: {video.get('comments', 0)}\n"
        f"Published: {video.get('published_at', '')}\n"
        f"Duration: {video.get('duration', '')}\n"
        f"Tags: {video.get('tags', [])}\n"
        f"Description: {video.get('description', '')[:500]}\n"
        f"Thumbnail Description: {video.get('thumbnail_description', 'N/A')}\n\n"
        f"TRANSCRIPT:\n{transcript[:15000]}\n\n"
        f"TOP COMMENTS:\n{comments_text[:5000]}"
    )

    result = _call_claude(client, prompt, user_msg)

    # Save to yt_viral_analyzer_results
    save_data = {
        "video_record_id": video["id"],
        "video_id": video["video_id"],
    }
    # Map top-level keys from the agent output
    for key in [
        "analysis_metadata", "title_analysis", "script_structure",
        "audience_intelligence", "visual_psychology",
        "viral_formula_synthesis", "human_readable_summary",
    ]:
        save_data[key] = result.get(key)

    supabase_client.table("yt_viral_analyzer_results").upsert(
        save_data, on_conflict="video_record_id"
    ).execute()

    logger.info("Agent 1 complete for %s", video["video_id"])
    return result


# ---------------------------------------------------------------------------
# Agent 2: Strategist
# ---------------------------------------------------------------------------

def run_agent2_strategist(
    supabase_client: Any,
    video: dict[str, Any],
    analyzer_result: dict[str, Any],
) -> dict[str, Any]:
    """Run Agent 2 — Strategist.

    Args:
        supabase_client: Supabase client.
        video: yt_viral_videos record.
        analyzer_result: Agent 1 output.

    Returns:
        Strategy JSON saved to yt_strategist_results.
    """
    logger.info("Running Agent 2 (Strategist) for %s", video["video_id"])
    client = _get_claude_client()
    prompt = _load_prompt(supabase_client, "agent2_strategist")

    user_msg = (
        f"ANALYZER OUTPUT:\n{json.dumps(analyzer_result, indent=2)[:20000]}"
    )

    result = _call_claude(client, prompt, user_msg)

    # Save to yt_strategist_results
    save_data = {
        "video_record_id": video["id"],
        "video_id": video["video_id"],
    }
    for key in [
        "strategy_brief", "title_options", "ranking_justification",
        "thumbnail_concept", "video_metadata", "script_writer_instructions",
    ]:
        val = result.get(key)
        if isinstance(val, str):
            save_data[key] = val
        else:
            save_data[key] = val  # jsonb columns accept dicts directly

    supabase_client.table("yt_strategist_results").upsert(
        save_data, on_conflict="video_record_id"
    ).execute()

    logger.info("Agent 2 complete for %s", video["video_id"])
    return result


# ---------------------------------------------------------------------------
# Agent 3: Script Writer
# ---------------------------------------------------------------------------

def run_agent3_script_writer(
    supabase_client: Any,
    video: dict[str, Any],
    analyzer_result: dict[str, Any],
    strategist_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run Agent 3 — Script Writer.

    Args:
        supabase_client: Supabase client.
        video: yt_viral_videos record.
        analyzer_result: Agent 1 output.
        strategist_result: Agent 2 output.

    Returns:
        List of sentence dicts [{sentence_number, sentence_text, section, ...}].
    """
    logger.info("Running Agent 3 (Script Writer) for %s", video["video_id"])
    client = _get_claude_client()
    prompt = _load_prompt(supabase_client, "agent3_script_writer")

    user_msg = (
        f"ANALYZER OUTPUT:\n{json.dumps(analyzer_result, indent=2)[:12000]}\n\n"
        f"STRATEGIST OUTPUT:\n{json.dumps(strategist_result, indent=2)[:12000]}"
    )

    result = _call_claude(client, prompt, user_msg)

    # Result should be a list or a dict with script_visual_breakdown key
    sentences = result
    if isinstance(result, dict):
        sentences = result.get("script_visual_breakdown", result.get("sentences", []))

    if not isinstance(sentences, list) or len(sentences) < 50:
        logger.warning(
            "ANNEALING: Agent 3 returned only %d sentences (min 150)",
            len(sentences) if isinstance(sentences, list) else 0,
        )
        # Retry with explicit length instruction
        user_msg += (
            "\n\nCRITICAL: You MUST produce at least 150 sentences. "
            "The script must be 1200-2250 words. This is a hard requirement."
        )
        result = _call_claude(client, prompt, user_msg, retry_on_json_error=True)
        if isinstance(result, dict):
            sentences = result.get("script_visual_breakdown", result.get("sentences", []))
        else:
            sentences = result

    logger.info("Agent 3 produced %d sentences for %s", len(sentences), video["video_id"])
    return sentences


# ---------------------------------------------------------------------------
# Agent 4: Optimizer
# ---------------------------------------------------------------------------

def run_agent4_optimizer(
    supabase_client: Any,
    video: dict[str, Any],
    sentences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run Agent 4 — Optimizer.

    Cleans sentences: no dashes/parentheses in sentence_text,
    facts verified, English only.

    Args:
        supabase_client: Supabase client.
        video: yt_viral_videos record.
        sentences: Agent 3 output.

    Returns:
        Cleaned list of sentence dicts.
    """
    logger.info("Running Agent 4 (Optimizer) for %s", video["video_id"])
    client = _get_claude_client()
    prompt = _load_prompt(supabase_client, "agent4_optimizer")

    user_msg = f"SCRIPT TO OPTIMIZE:\n{json.dumps(sentences, indent=2)}"

    result = _call_claude(client, prompt, user_msg)

    optimized = result
    if isinstance(result, dict):
        optimized = result.get("script_visual_breakdown", result.get("sentences", []))

    logger.info(
        "Agent 4 optimized %d sentences for %s",
        len(optimized), video["video_id"],
    )
    return optimized


# ---------------------------------------------------------------------------
# Save final script
# ---------------------------------------------------------------------------

def save_script_to_db(
    supabase_client: Any,
    video_record_id: str,
    sentences: list[dict[str, Any]],
) -> int:
    """Save optimized sentences to yt_scripts table.

    Clears any existing sentences for this video, then inserts fresh.

    Args:
        supabase_client: Supabase client.
        video_record_id: UUID of the yt_viral_videos record.
        sentences: Final optimized sentence list.

    Returns:
        Number of sentences inserted.
    """
    # Delete existing scripts for this video
    supabase_client.table("yt_scripts").delete().eq(
        "viral_video_id", video_record_id
    ).execute()

    rows = [
        {
            "viral_video_id": video_record_id,
            "sentence_number": s.get("sentence_number", i + 1),
            "sentence_text": s.get("sentence_text", ""),
            "section": s.get("section", ""),
            "original_comparison": s.get("original_comparison", ""),
        }
        for i, s in enumerate(sentences)
    ]

    # Insert in batches of 50
    for i in range(0, len(rows), 50):
        batch = rows[i : i + 50]
        supabase_client.table("yt_scripts").insert(batch).execute()

    logger.info(
        "Saved %d sentences to yt_scripts for %s",
        len(rows), video_record_id,
    )
    return len(rows)
