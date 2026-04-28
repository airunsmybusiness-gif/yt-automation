"""Gemini 2.5 Flash text generator — drop-in replacement for Anthropic calls."""
import os
import logging
from typing import Optional
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ["GEMINI_API_KEY"]
        _client = genai.Client(api_key=api_key)
    return _client


def generate_text(
    system_prompt: str,
    user_prompt: str,
    model: str = "gemini-2.5-flash",
    max_tokens: int = 8192,
    temperature: float = 1.0,
) -> str:
    """Generate text via Gemini. Mirrors Anthropic messages.create interface."""
    client = _get_client()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=max_tokens,
        temperature=temperature,
    )
    resp = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=config,
    )
    if not resp.text:
        raise RuntimeError(f"Gemini returned empty response: {resp}")
    return resp.text


class GeminiMessageShim:
    """Anthropic SDK compatibility shim. Lets pipeline.py keep its existing call shape."""
    def __init__(self):
        pass

    @property
    def messages(self):
        return self

    def create(self, model=None, max_tokens=4096, system="", messages=None, **kwargs):
        user_text = ""
        if messages:
            for m in messages:
                if m.get("role") == "user":
                    content = m.get("content", "")
                    if isinstance(content, list):
                        user_text += "\n".join(
                            b.get("text", "") for b in content if b.get("type") == "text"
                        )
                    else:
                        user_text += content
        text = generate_text(
            system_prompt=system or "",
            user_prompt=user_text,
            max_tokens=max_tokens,
        )

        class _Block:
            def __init__(self, text):
                self.text = text
                self.type = "text"

        class _Response:
            def __init__(self, text):
                self.content = [_Block(text)]
                self.stop_reason = "end_turn"

        return _Response(text)
