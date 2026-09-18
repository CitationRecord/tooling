"""Grok, through xAI's OpenAI-compatible Chat Completions.

**Grok is not in the published scope of Edition One.** The page commits to
Claude, GPT-5 and Gemini. This adapter exists so a pilot can find out whether
Grok is different enough to be worth changing that, which is a question a pilot
is for and not one this code answers. Every transcript it produces carries
`published_scope: false`.

Retrieval is off because no tools are declared, which is now the only way to
say it. An earlier version of this adapter set `search_parameters.mode` to
`off` explicitly, on the principle that a default which happens to be right
today records nothing. xAI has since removed Live Search altogether: the
parameter returns HTTP 410 pointing at the Agent Tools API, so sending it fails
the request rather than documenting anything.

The Agent Tools API is opt-in and declared through `tools`, so an absent
`tools` key is a positive statement here in the same way it is for the other
three. `search_parameters` must not be reintroduced: it is not a stricter
setting, it is a rejected one.
"""

from __future__ import annotations

from . import dig

API_BASE = "https://api.x.ai/v1"

RETRIEVAL_OFF = (
    "no `tools` key in the request body, so the Agent Tools API is not "
    "engaged and no search tool is available to the model. Live Search, "
    "which this adapter previously disabled by name, was removed by xAI; "
    "sending `search_parameters` now returns HTTP 410"
)

#: xAI follows the older OpenAI field name.
MAX_TOKENS_FIELD = "max_tokens"

#: Removed by the vendor. Named here so that a future edit reintroducing it
#: meets this note rather than rediscovering the 410.
REMOVED_BY_VENDOR = ("search_parameters",)


def build(block: dict, query_text: str, system_prompt, key: str) -> tuple:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": query_text})

    body = {
        "model": block["model"],
        MAX_TOKENS_FIELD: block["max_output_tokens"],
        "messages": messages,
    }
    for name, value in (block.get("sampling", {}).get("requested") or {}).items():
        body[name] = value
    for name, value in (block.get("extra") or {}).items():
        if name in REMOVED_BY_VENDOR:
            continue
        body[name] = value

    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "authorization": f"Bearer {key}",
    }
    return "POST", f"{API_BASE}/chat/completions", headers, body


def extract(payload) -> dict:
    content = dig(payload, "choices", 0, "message", "content")
    return {
        "text": content if isinstance(content, str) else None,
        "reported_model": dig(payload, "model"),
        "response_id": dig(payload, "id"),
        "stop_reason": dig(payload, "choices", 0, "finish_reason"),
        "stop_detail": dig(payload, "choices", 0, "message", "refusal"),
        "usage": dig(payload, "usage"),
        "api_error": dig(payload, "error"),
    }
