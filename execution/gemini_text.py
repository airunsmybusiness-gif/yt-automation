"""Anthropic Claude Sonnet text generator — Anthropic SDK compatibility shim.

Drop-in replacement for the prior Groq shim. Same class name and method
signature so orchestration/pipeline.py needs no changes.
"""

import logging
import os
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
MAX_INPUT_CHARS = 180_000  # ~45k tokens, well under Sonnet's 200k window
MAX_OUTPUT_TOKENS = 4096


class GeminiMessageShim:
    """Anthropic-backed shim. Class name preserved for backwards compatibility."""

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> str:
        """Generate text via Claude Sonnet. Truncates user_prompt defensively."""
        if len(user_prompt) > MAX_INPUT_CHARS:
            logger.warning(
                "User prompt %d chars exceeds %d, truncating",
                len(user_prompt), MAX_INPUT_CHARS,
            )
            user_prompt = user_prompt[:MAX_INPUT_CHARS]

        try:
            resp = self._client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIStatusError as e:
            logger.error("Anthropic API error: %s", e)
            raise RuntimeError(f"Anthropic call failed: {e}") from e

        if not resp.content or not resp.content[0].text:
            raise RuntimeError(f"Anthropic returned empty: {resp}")

        return resp.content[0].text
