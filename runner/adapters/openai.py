"""GPT-5, through Chat Completions.

Chat Completions rather than the newer Responses API, for one reason: this
component's whole value is that the request and the response are recorded as
bytes, and Chat Completions is the surface whose shape is most widely
understood by anyone who might later check the record. A benchmark's transport
should be the boring option.

No `tools`, and no hosted web search, file search or retrieval tool declared,
which is what makes this a raw model.

The output ceiling is `max_completion_tokens`, not `max_tokens`: the reasoning
models rejected the older field. Named here as one constant so a vendor
renaming it is a one-line change rather than a hunt.
"""

from __future__ import annotations

from . import dig

API_BASE = "https://api.openai.com/v1"

RETRIEVAL_OFF = (
    "no `tools` key in the request body, so no hosted web search, file search "
    "or retrieval tool is available to the model"
)

#: The vendor's name for the output ceiling. `max_tokens` is rejected by the
#: reasoning models.
MAX_TOKENS_FIELD = "max_completion_tokens"


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
