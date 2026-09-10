"""The append-only registration journal.

One JSON object per line, exactly as `citation-resolutions/lookups.jsonl` does
it, and in the same repository for the same reason: a claim that an artifact
was fixed on a date is worth something only if the record of that fixing can
be reached.

Records are appended, never edited. An artifact that changes is registered
again, naming what it supersedes, and both records are kept. Every record
carries the hash of the line before it, so removing or rewriting a record
breaks the chain at that point rather than passing unnoticed.

The full path of the artifact is deliberately not recorded. The filename is
enough to identify it and a path can say more about an unpublished artifact
than its owner intended.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA, __version__
from .digest import Digest, sha256_text


def iso_utc() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def run_provenance() -> dict:
    """Shared with the resolver, minus the fields describing an API client."""
    from resolve.journal import run_provenance as base

    provenance = base()
    for key in ("api", "eyecite_version", "reporters_db_version",
                "resolver_version", "user_agent"):
        provenance.pop(key, None)
    provenance["register_version"] = __version__
    return provenance


def read_records(path) -> list:
    """Every record in the journal, in order. Missing file reads as empty."""
    path = Path(path)
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def last_line_hash(path) -> str | None:
    """The chain head: hash of the final line, or None for an empty journal."""
    path = Path(path)
    if not path.is_file():
        return None
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return sha256_text(lines[-1]) if lines else None


def verify_chain(path) -> tuple:
    """Walk the chain. Returns (ok, index_of_first_break, detail)."""
    path = Path(path)
    if not path.is_file():
        return True, None, "no journal"
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    expected = None
    for index, line in enumerate(lines):
        record = json.loads(line)
        if record.get("prev_hash") != expected:
            return False, index, (
                f"record {index} claims prev_hash {record.get('prev_hash')!r}, "
                f"chain says {expected!r}"
            )
        expected = sha256_text(line)
    return True, None, f"{len(lines)} record(s), chain intact"


def build_record(kind: str, edition: str, filename: str, digest: Digest,
                 prev_hash, note=None, supersedes=None, label=None) -> dict:
    return {
        "schema": SCHEMA,
        "record_id": secrets.token_hex(8),
        "kind": kind,
        "edition": edition,
        "label": label,
        "artifact": {"filename": filename, **digest.as_dict()},
        "controls": digest.controlling_kind,
        "registered_at_utc": iso_utc(),
        "supersedes": supersedes,
        "note": note,
        "prev_hash": prev_hash,
        "provenance": run_provenance(),
    }


def append(path, record: dict) -> dict:
    """Append one record, atomically enough that a kill cannot half-write it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def find_by_digest(path, digest: Digest) -> list:
    """Records whose artifact matches this digest, newest last.

    Matched on the controlling hash. A canonical match with a different byte
    hash is a match: the data is identical and the bytes were reformatted,
    which is what the canonical form exists to tolerate.
    """
    matches = []
    for record in read_records(path):
        artifact = record.get("artifact") or {}
        if digest.sha256_canonical and artifact.get("sha256_canonical"):
            if artifact["sha256_canonical"] == digest.sha256_canonical:
                matches.append(record)
        elif artifact.get("sha256_bytes") == digest.sha256_bytes:
            matches.append(record)
    return matches
