"""Append-only JSONL manifest.

Design constraint: raw captures are immutable. Records are appended, never
rewritten. A correction is a new capture, and both are retained.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class ManifestLine:
    line_no: int
    record: dict


def append(manifest_path: Path, record: dict) -> None:
    """Append one record. One JSON object per line, newline terminated."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with open(manifest_path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read(manifest_path: Path) -> Iterator[ManifestLine]:
    """Yield every well-formed record. Malformed lines are skipped."""
    if not Path(manifest_path).is_file():
        return
    with open(manifest_path, "r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield ManifestLine(line_no=line_no, record=record)


def records(manifest_path: Path) -> list[dict]:
    return [entry.record for entry in read(manifest_path)]


def last_capture_by_claim(manifest_path: Path) -> dict[str, dict]:
    """Most recent record per claim id that actually captured content.

    Blocked and failed attempts are recorded but are not a baseline to
    compare a later hash against.
    """
    latest: dict[str, dict] = {}
    for entry in read(manifest_path):
        record = entry.record
        claim_id = record.get("claim_id")
        if claim_id and record.get("content_sha256"):
            latest[claim_id] = record
    return latest


def last_record_by_claim(manifest_path: Path) -> dict[str, dict]:
    """Most recent record per claim id of any status."""
    latest: dict[str, dict] = {}
    for entry in read(manifest_path):
        claim_id = entry.record.get("claim_id")
        if claim_id:
            latest[claim_id] = entry.record
    return latest


def write_once(path: Path, data: bytes) -> Path:
    """Write a snapshot file and mark it read-only.

    Refuses to overwrite. Snapshot paths carry a UTC timestamp, so a
    collision means something is wrong with the clock or the run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing snapshot file: {path}")
    with open(path, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, stat.S_IREAD)
    return path
