"""Claude, through the Messages API.

Two settings here are deliberate and both are recorded rather than assumed.

**No `tools` array at all.** Not an empty one: the field is absent, so there is
no web search, no web fetch, no MCP connector and no code execution. That is
what makes this a raw model.

**No server-side `fallbacks`.** The parameter routes a refused request to a
different model, which is the sensible default for an application and the wrong
thing entirely for a benchmark. A transcript labelled Claude that was answered
by another model destroys the only thing the record is for. A refusal arrives
as HTTP 200 with `stop_reason: "refusal"` and is recorded as the answer it is.

Sampling is the awkward one. Claude Opus 5 does not accept `temperature`,
`top_p` or `top_k` at all -- they were removed with the adaptive-thinking
models and the API returns 400. There is no temperature to record, and writing
`temperature: 0` into a provenance record because the protocol asked for one
would be a false statement. The protocol declares them unsupported for this
system and the transcript keeps `requested`, `as_sent` and `unsupported` apart.
"""

from __future__ import annotations

import os

from . import dig, joined_text

API_BASE = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"

#: Optional. Required only when the API key is not itself scoped to a
#: workspace, in which case the API refuses the request with a 400 naming it.
WORKSPACE_ENV_VAR = "ANTHROPIC_WORKSPACE_ID"

RETRIEVAL_OFF = (
    "no `tools` key in the request body, so no web_search, web_fetch, "
    "mcp_servers or code_execution is available to the model"
)

#: The vendor's name for the output ceiling.
MAX_TOKENS_FIELD = "max_tokens"


def build(block: dict, query_text: str, system_prompt, key: str) -> tuple:
    """One request. `block` is this system's section of the frozen protocol."""
    body = {
        "model": block["model"],
        MAX_TOKENS_FIELD: block["max_output_tokens"],
        "messages": [{"role": "user", "content": query_text}],
    }
    if system_prompt:
        body["system"] = system_prompt
    for name, value in (block.get("sampling", {}).get("requested") or {}).items():
        body[name] = value
    for name, value in (block.get("extra") or {}).items():
        body[name] = value

    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "x-api-key": key,
        "anthropic-version": API_VERSION,
    }
    # An API key that is not scoped to a workspace is refused with a 400 unless
    # the workspace is named in a header. Read from the environment rather than
    # the protocol: it identifies an account, not a thing being measured, and
    # nothing about which workspace paid for a call belongs in a frozen
    # protocol or in a transcript.
    workspace = (os.environ.get(WORKSPACE_ENV_VAR) or "").strip()
    if workspace:
        headers["anthropic-workspace-id"] = workspace
    return "POST", f"{API_BASE}/messages", headers, body


def extract(payload) -> dict:
    """The assistant turn and the identifiers, by field access only."""
    return {
        "text": joined_text(dig(payload, "content"), "type", "text", "text"),
        "reported_model": dig(payload, "model"),
        "response_id": dig(payload, "id"),
        "stop_reason": dig(payload, "stop_reason"),
        "stop_detail": dig(payload, "stop_details"),
        "usage": dig(payload, "usage"),
        "api_error": dig(payload, "error"),
    }
