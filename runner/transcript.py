"""Write-once storage for raw model outputs, and the chained index over them.

Three properties, each for a stated reason.

**Exclusive creation.** A transcript is created with O_EXCL, so a second write
to the same path fails rather than overwrites. A re-run produces a new attempt
number and both are kept, per the tooling README: an output that needs
correcting is re-run, and both runs are retained.

**Hashed after the close.** The hash goes into the index only once the file is
flushed, fsynced and closed. A process killed mid-write therefore leaves a file
that fails verification loudly, rather than a file whose hash was computed from
what was meant to be written.

**Chained.** Every index line carries the SHA-256 of the line before it, as
`lookups.jsonl` and `registrations.jsonl` do, so removing or rewriting a line
breaks the chain at that point instead of passing unnoticed.

Sealing the file read-only afterwards stops accidents, not adversaries, and it
is worth being clear which.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path

from register.digest import sha256_text
from register.journal import read_records

from . import RUN_SCHEMA, SCHEMA, __version__
from .config import check_no_repository

INDEX_NAME = "transcripts.jsonl"
RUN_NAME = "run.json"
PROTOCOL_SNAPSHOT = "protocol.snapshot.json"
QUERYSET_SNAPSHOT = "queryset.snapshot.json"
TRANSCRIPT_DIR = "transcripts"


class AlreadyWritten(FileExistsError):
    """Something tried to write a transcript that already exists."""


def iso_utc() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def provenance() -> dict:
    """Run provenance, shared with the resolver, minus the API client fields."""
    from resolve.journal import run_provenance

    base = run_provenance()
    for key in ("api", "eyecite_version", "reporters_db_version",
                "resolver_version", "user_agent"):
        base.pop(key, None)
    base["runner_version"] = __version__
    return base


def new_run_id(label: str, pilot: bool) -> str:
    """A run identifier that says what kind of run it was in its own name.

    A pilot run is named `pilot-...` so that a directory listing, a filename in
    a report and a half-remembered path all carry the fact. Nothing downstream
    has to look inside a file to find out what it is looking at.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # A pilot edition label usually already begins with "pilot", and
    # "pilot-pilot-2026.09-..." reads like a bug in the thing that named it.
    # The point of the prefix is that the directory says what it is, which one
    # copy achieves.
    prefix = "" if (not pilot or label.lower().startswith("pilot")) else "pilot-"
    return f"{prefix}{label}-{stamp}-{secrets.token_hex(3)}"


def write_once(path: Path, text: str, seal: bool = True) -> str:
    """Create a file that did not exist, and return the hash of what was written.

    Raises if it existed. The hash is computed from the bytes after they are
    on disk, not from the string that was meant to reach it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        handle = os.open(path, flags)
    except FileExistsError:
        raise AlreadyWritten(
            f"{path.name} already exists. Transcripts are written once. A "
            f"re-run is a new attempt and keeps both.") from None
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # A failed write leaves a file that verification will reject. Do not
        # remove it: a truncated transcript is evidence of what happened.
        raise
    if seal:
        try:
            os.chmod(path, stat.S_IREAD)
        except OSError:
            pass
    return sha256_text(path.read_bytes().decode("utf-8"))


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


class RunStore:
    """One run directory. Created once, appended to, never rewritten."""

    def __init__(self, root, run_id: str, seal: bool = True) -> None:
        self.root = Path(check_no_repository(Path(root) / run_id))
        self.run_id = run_id
        self.seal = seal
        self.root.mkdir(parents=True, exist_ok=False)
        (self.root / TRANSCRIPT_DIR).mkdir()
        self._seq = 0

    # -- paths -------------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.root / INDEX_NAME

    def transcript_path(self, query_id: str, system_id: str, attempt: int) -> Path:
        name = f"{query_id}__{system_id}__{attempt:02d}.json"
        return self.root / TRANSCRIPT_DIR / name

    def next_attempt(self, query_id: str, system_id: str) -> int:
        """The lowest attempt number not already on disk for this pair."""
        attempt = 1
        while self.transcript_path(query_id, system_id, attempt).exists():
            attempt += 1
        return attempt

    # -- writing -----------------------------------------------------------

    def snapshot(self, name: str, payload: dict) -> str:
        """A byte copy of an input, so the run does not depend on finding it."""
        return write_once(self.root / name, _dump(payload), seal=self.seal)

    def manifest(self, payload: dict) -> str:
        return write_once(self.root / RUN_NAME, _dump(payload), seal=self.seal)

    def _last_line_hash(self):
        if not self.index_path.is_file():
            return None
        lines = [l for l in self.index_path.read_text(
            encoding="utf-8").splitlines() if l.strip()]
        return sha256_text(lines[-1]) if lines else None

    def record(self, transcript: dict) -> dict:
        """Write one transcript, then index it. In that order, deliberately."""
        query_id = transcript["query"]["id"]
        system_id = transcript["system"]["id"]
        attempt = transcript["attempt"]
        path = self.transcript_path(query_id, system_id, attempt)

        digest = write_once(path, _dump(transcript), seal=self.seal)

        self._seq += 1
        line = {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "seq": self._seq,
            "query_id": query_id,
            "system_id": system_id,
            "attempt": attempt,
            "transcript": path.name,
            "sha256_bytes": digest,
            "outcome": transcript.get("outcome"),
            "pilot": transcript.get("pilot", False),
            "published_scope": transcript["system"].get("published_scope"),
            "logged_at_utc": iso_utc(),
            "prev_hash": self._last_line_hash(),
        }
        raw = json.dumps(line, ensure_ascii=False, sort_keys=True)
        with open(self.index_path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(raw + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return line


# -- verification ---------------------------------------------------------


def verify(run_root) -> dict:
    """Rehash every transcript, walk the chain, and name what is missing."""
    root = Path(run_root)
    index = root / INDEX_NAME
    report = {
        "run_id": root.name,
        "lines": 0,
        "chain_ok": True,
        "chain_break": None,
        "mismatched": [],
        "missing": [],
        "unindexed": [],
    }
    if not index.is_file():
        report["chain_ok"] = False
        report["chain_break"] = "no index"
        return report

    lines = [l for l in index.read_text(encoding="utf-8").splitlines() if l.strip()]
    report["lines"] = len(lines)
    expected_prev = None
    indexed = set()

    for position, raw in enumerate(lines):
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            report["chain_ok"] = False
            report["chain_break"] = f"line {position} is not JSON"
            break
        if entry.get("prev_hash") != expected_prev and report["chain_ok"]:
            report["chain_ok"] = False
            report["chain_break"] = (
                f"line {position} claims prev_hash {entry.get('prev_hash')!r}, "
                f"chain says {expected_prev!r}")
        expected_prev = sha256_text(raw)

        name = entry.get("transcript")
        indexed.add(name)
        path = root / TRANSCRIPT_DIR / str(name)
        if not path.is_file():
            report["missing"].append(name)
            continue
        actual = sha256_text(path.read_bytes().decode("utf-8", errors="replace"))
        if actual != entry.get("sha256_bytes"):
            report["mismatched"].append(name)

    directory = root / TRANSCRIPT_DIR
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            if path.name not in indexed:
                report["unindexed"].append(path.name)
    return report


def coverage(run_root) -> dict:
    """Which query-by-system cells produced a transcript, and which did not."""
    root = Path(run_root)
    manifest_path = root / RUN_NAME
    if not manifest_path.is_file():
        return {"expected": 0, "present": 0, "absent": []}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    queries = [q["id"] for q in manifest.get("queries", [])]
    systems = [s["id"] for s in manifest.get("systems", [])]

    present = set()
    index = root / INDEX_NAME
    if index.is_file():
        for raw in index.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            entry = json.loads(raw)
            present.add((entry.get("query_id"), entry.get("system_id")))

    absent = [f"{q}/{s}" for q in queries for s in systems
              if (q, s) not in present]
    return {
        "expected": len(queries) * len(systems),
        "present": len(present),
        "absent": absent,
    }


def read_index(run_root) -> list:
    """Every index line, in order."""
    return read_records(Path(run_root) / INDEX_NAME)
