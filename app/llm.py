"""A small client for any OpenAI-compatible chat API with tool calling (DeepSeek by default).

Without a key the app still runs: the agent follows a fixed plan and CareerKit
uses simpler methods (keyword matching, a template letter). The page says so.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import settings


class LLMError(Exception):
    pass


def enabled() -> bool:
    return bool(settings.llm_api_key)


def chat(messages: list[dict], *, tools: list[dict] | None = None, json_mode: bool = False, temperature: float = 0.4) -> dict:
    """One chat completion. Returns the assistant message (with any tool_calls)."""
    if not enabled():
        raise LLMError("No LLM_API_KEY set")
    body: dict[str, Any] = {"model": settings.llm_model, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        response = httpx.post(
            f"{settings.llm_base_url}/chat/completions",
            json=body,
            headers={"authorization": f"Bearer {settings.llm_api_key}"},
            timeout=120,
        )
    except httpx.HTTPError as error:
        raise LLMError(f"Model unreachable: {error}") from error
    if response.status_code >= 400:
        raise LLMError(f"Model answered {response.status_code}: {response.text[:200]}")
    return response.json()["choices"][0]["message"]


def ask_json(system: str, user: str) -> dict:
    """Asks for a JSON object back."""
    message = chat(
        [{"role": "system", "content": system + "\nAnswer with a single JSON object."}, {"role": "user", "content": user}],
        json_mode=True,
        temperature=0.2,
    )
    try:
        return json.loads(message.get("content") or "{}")
    except json.JSONDecodeError as error:
        raise LLMError("The model didn't return JSON") from error


def ask_text(system: str, user: str, temperature: float = 0.6) -> str:
    message = chat([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=temperature)
    return (message.get("content") or "").strip()
