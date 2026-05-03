"""Anthropic Claude Sonnet shim — exposes native .messages.create() interface.

Drop-in replacement for the prior Groq shim. Class name preserved so
orchestration/pipeline.py needs no changes. Calls like
`self.ai.messages.create(model=..., messages=[...])` work directly.
"""

import logging
import os

import anthropic

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")


class GeminiMessageShim:
    """Thin wrapper around anthropic.Anthropic that exposes .messages directly."""

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.default_model = DEFAULT_MODEL

    @property
    def messages(self) -> anthropic.resources.messages.Messages:
        """Passthrough to the Anthropic SDK's messages resource."""
        return self._client.messages
