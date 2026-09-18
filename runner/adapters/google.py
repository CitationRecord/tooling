"""Gemini, through generateContent.

No `tools`, and in particular no `google_search` / `googleSearchRetrieval`
grounding tool, which is what makes this a raw model. Gemini is the system
where the distinction matters most visibly: grounded, it is a retrieval product
and would be measuring something this edition does not claim to measure.

The credential goes in the `x-goog-api-key` header rather than the `?key=`
query parameter the quickstarts use. A URL is recorded on every transcript and
a credential in a URL is a credential in a file that is written once and never
edited.
"""

from __future__ import annotations

from . import dig, joined_text

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

RETRIEVAL_OFF = (
    "no `tools` key in the request body, so no google_search grounding tool "
    "is available to the model"
)

#: The vendor's name for the output ceiling, inside generationConfig.
MAX_TOKENS_FIELD = "maxOutputTokens"


def build(block: dict, query_text: str, system_prompt, key: str) -> tuple:
    generation = {MAX_TOKENS_FIELD: block["max_output_tokens"]}
    for name, value in (block.get("sampling", {}).get("requested") or {}).items():
        generation[name] = value

    body = {
        "contents": [{"role": "user", "parts": [{"text": query_text}]}],
        "generationConfig": generation,
    }
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    for name, value in (block.get("extra") or {}).items():
        body[name] = value

    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "x-goog-api-key": key,
    }
    url = f"{API_BASE}/models/{block['model']}:generateContent"
    return "POST", url, headers, body


def extract(payload) -> dict:
    parts = dig(payload, "candidates", 0, "content", "parts")
    return {
        # Gemini's parts carry no type discriminator, so every part with a
        # `text` key is text. `joined_text` with no type filter does that.
        "text": joined_text(parts, None, None, "text"),
        "reported_model": dig(payload, "modelVersion"),
        "response_id": dig(payload, "responseId"),
        "stop_reason": dig(payload, "candidates", 0, "finishReason"),
        "stop_detail": dig(payload, "promptFeedback"),
        "usage": dig(payload, "usageMetadata"),
        "api_error": dig(payload, "error"),
    }
