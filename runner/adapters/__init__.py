"""One adapter per vendor. Each builds a request and reads one field back.

The contract is deliberately small, and the same for all four:

    RETRIEVAL_OFF   the exact request setting that makes this a raw model,
                    named so a reader can check it against the stored request
                    rather than trust the word "raw"
    build(...)      -> (method, url, headers, body dict)
    extract(...)    -> the assistant turn and the identifiers, by field access

**`extract` is field access, never pattern matching.** It reaches into the
vendor's JSON envelope for the assistant turn and the model string, and it does
nothing else to the response. The complete body is stored regardless, so a
malformed envelope costs nothing: `extract` returns what it could not find as
None and the raw bytes are still on disk.
"""

from __future__ import annotations


def dig(payload, *path, default=None):
    """Walk a nested structure by key and index, tolerating any shape.

    A malformed response is data, so nothing here may raise on one. Every
    step that cannot be taken yields the default.
    """
    current = payload
    for step in path:
        if isinstance(step, int):
            if not isinstance(current, (list, tuple)) or len(current) <= step:
                return default
            current = current[step]
        else:
            if not isinstance(current, dict) or step not in current:
                return default
            current = current[step]
    return default if current is None else current


def joined_text(blocks, type_key: str, type_value: str, text_key: str) -> str:
    """Concatenate the text-bearing blocks of a content array, in order.

    Joined rather than truncated to the first: a response split across several
    blocks is one answer, and keeping only the first would be a silent edit.
    """
    if not isinstance(blocks, (list, tuple)):
        return None
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if type_key and block.get(type_key) != type_value:
            continue
        text = block.get(text_key)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts) if parts else None
