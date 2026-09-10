"""Hashing an artifact, in a form that survives being stored.

Two hashes, because one is not enough and the reason is practical rather than
theoretical.

The byte hash is what was actually on disk at registration. It is exact and it
is fragile: git normalises line endings, editors add and remove trailing
newlines, and a byte hash that breaks for those reasons is indistinguishable
from one that breaks because the artifact was altered. A control that cries
wolf is not a control.

The canonical hash is taken over the artifact re-serialised as sorted, compact,
UTF-8 JSON with no trailing newline. It ignores every difference that does not
change the data, and it is the control. It exists only for artifacts that parse
as JSON, which is why query sets are written as JSON.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import CANONICAL_FORM


@dataclass(frozen=True)
class Digest:
    """What a file hashed to, and how."""

    bytes_len: int
    sha256_bytes: str
    sha256_canonical: str | None
    canonical_form: str | None

    @property
    def controlling(self) -> str:
        """The hash a verification compares against."""
        return self.sha256_canonical or self.sha256_bytes

    @property
    def controlling_kind(self) -> str:
        return "canonical" if self.sha256_canonical else "bytes"

    def as_dict(self) -> dict:
        return {
            "bytes": self.bytes_len,
            "sha256_bytes": self.sha256_bytes,
            "sha256_canonical": self.sha256_canonical,
            "canonical_form": self.canonical_form,
        }


def canonical_json(payload) -> bytes:
    """The one serialisation this project hashes JSON in.

    Sorted keys, no insignificant whitespace, UTF-8, no trailing newline. A
    third party recomputes it from this description without reading the code.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_bytes(raw: bytes) -> Digest:
    """Hash a byte string, canonically too where it is JSON."""
    sha_bytes = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Digest(len(raw), sha_bytes, None, None)
    sha_canonical = hashlib.sha256(canonical_json(payload)).hexdigest()
    return Digest(len(raw), sha_bytes, sha_canonical, CANONICAL_FORM)


def digest_file(path) -> Digest:
    """Hash a file on disk. Read whole: an artifact too large to hold in
    memory is too large to be a query set, and pretending otherwise would add
    streaming complexity to protect a case that should not arise."""
    return digest_bytes(Path(path).read_bytes())


def sha256_text(text: str) -> str:
    """Hash one journal line, for the chain."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
