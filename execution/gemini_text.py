"""Groq Llama 3.3 70B text generator — Anthropic SDK compatibility shim."""
import os
import logging
import requests

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"


def generate_text(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 8192,
    temperature: float = 1.0,
) -> str:
    """Generate text via Groq."""
    messages = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    r = requests.post(GROQ_URL, json=payload, headers=headers, timeout=120)
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"]
    if not text:
        raise RuntimeError(f"Groq returned empty: {data}")
    return text


class GeminiMessageShim:
    """Anthropic SDK compatibility shim — name kept for backward compat."""
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
