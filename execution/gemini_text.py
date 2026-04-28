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
        # Force AI Studio endpoint, not Vertex AI
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
        api_key = os.environ["GEMINI_API_KEY"]
        _client = genai.Client(api_key=api_key, vertexai=False)
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
    config_kwargs = {
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_prompt and system_prompt.strip():
        config_kwargs["system_instruction"] = system_prompt
    config = types.GenerateContentConfig(**config_kwargs)
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
                role = m.get("role", "")
                content = m.get("content", "")
                if isinstance(content, list):
                    parts = []
                    for b in content:
                        if isinstance(b, dict):
                            if b.get("type") == "text":
                                parts.append(b.get("text", ""))
                        elif isinstance(b, str):
                            parts.append(b)
                    chunk = "\n".join(parts)
                else:
                    chunk = str(content) if content else ""
                if role == "user":
                    user_text += chunk + "\n"
                elif role == "assistant":
                    user_text += f"[Previous assistant turn]: {chunk}\n"
        if not user_text.strip():
            raise RuntimeError(f"Empty user text. messages={messages}")
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
