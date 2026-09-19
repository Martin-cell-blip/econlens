"""Minimal OpenAI-compatible chat client. Works with DeepSeek / Kimi / Qwen / OpenAI.

Configuration (env):
    ECONLENS_API_KEY   - required to enable AI feedback
    ECONLENS_BASE_URL  - default https://api.deepseek.com
    ECONLENS_MODEL     - default deepseek-chat
"""
from __future__ import annotations

import json
import os

import requests

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT_S = 120


class LLMNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("ECONLENS_API_KEY"))


def chat(messages: list[dict], temperature: float = 0.2) -> str:
    """Return assistant message content. Raises LLMNotConfigured without a key."""
    api_key = os.environ.get("ECONLENS_API_KEY")
    if not api_key:
        raise LLMNotConfigured(
            "Set ECONLENS_API_KEY (and optionally ECONLENS_BASE_URL / ECONLENS_MODEL) to enable AI feedback."
        )
    base_url = os.environ.get("ECONLENS_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("ECONLENS_MODEL", DEFAULT_MODEL)
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        },
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model response (tolerates code fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model response")
    return json.loads(text[start:end + 1])
