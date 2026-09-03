"""Append-only lookup log.

Every lookup is logged with a UTC timestamp, whether it hit the network or
the cache, per the provenance constraint: a result without provenance is not
a result. One JSON object per line, appended, never rewritten.

Nothing written here carries credentials. Every string that could have come
from an exception passes through `redact` first.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import SCHEMA, USER_AGENT, __version__
from .client import redact
from .config import REPO_ROOT


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime | None = None) -> str:
    moment = moment or utc_now()
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True,
            text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def run_provenance(methodology_version: str | None = None) -> dict:
    """Provenance shared by every lookup in one run. No credentials."""
    try:
        import importlib.metadata as metadata

        eyecite_version = metadata.version("eyecite")
        reporters_version = metadata.version("reporters-db")
    except Exception:
        eyecite_version = reporters_version = None

    status = _git("status", "--porcelain")
    return {
        "resolver_version": __version__,
        "tooling": {
            "commit": _git("rev-parse", "HEAD"),
            "dirty": bool(status) if status is not None else None,
        },
        "methodology_version": methodology_version,
        "api": "courtlistener/v4",
        "user_agent": USER_AGENT,
        "eyecite_version": eyecite_version,
        "reporters_db_version": reporters_version,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }


class Journal:
    """Append-only JSONL log of every lookup."""

    def __init__(self, path: Path, provenance: dict | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._provenance = provenance or run_provenance()

    def record(self, resolution, calls=None, note: str | None = None) -> dict:
        entry = {
            "schema": SCHEMA,
            "logged_at_utc": iso_utc(),
            "query": resolution.query,
            "normalized_citation": resolution.normalized_citation,
            "status": resolution.status,
            "reason": resolution.reason,
            "explanation": redact(resolution.explanation or ""),
            "scorable": resolution.is_scorable,
            "from_cache": resolution.from_cache,
            "cached_at_utc": resolution.cached_at_utc,
            "retrieved_at_utc": resolution.retrieved_at_utc,
            "cluster_id": resolution.cluster_id,
            "coverage": resolution.coverage,
            "discrepancies": resolution.discrepancies,
            "api_calls": [
                {
                    "endpoint": call.endpoint,
                    "method": call.method,
                    "http_status": call.http_status,
                    "duration_ms": call.duration_ms,
                    "throttled": call.throttled,
                    "error": redact(call.error) if call.error else None,
                }
                for call in (calls or [])
            ],
            "note": note,
            "provenance": self._provenance,
        }
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def read(self) -> Iterator[dict]:
        if not self.path.is_file():
            return
        with open(self.path, "r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    yield entry
