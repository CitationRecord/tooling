"""Resumable downloads with integrity checks and a manifest.

Files run to gigabytes over a connection that will drop, so a download that
cannot resume is a download that may never finish. Bytes land in a `.part`
file and a restart continues with an HTTP Range request from wherever it
stopped.

Integrity is checked three ways: the byte count must match what the bucket
listed, the ETag is verified when it is a plain MD5, and a SHA-256 of the
finished file goes in the manifest so a later run can prove the file on disk
is the file that was fetched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import SCHEMA, USER_AGENT, __version__

CHUNK = 1024 * 1024


def iso_utc(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class IntegrityError(RuntimeError):
    """What arrived is not what the bucket said was there."""


@dataclass
class FileRecord:
    key: str
    object_key: str
    path: str
    bytes: int
    sha256: str
    etag: str | None
    etag_verified: bool
    last_modified: str | None
    downloaded_at_utc: str
    resumed: bool = False
    purpose: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Manifest:
    """What was fetched, from which generation, and proof of it."""

    generation: str
    directory: str
    created_at_utc: str = field(default_factory=iso_utc)
    updated_at_utc: str = field(default_factory=iso_utc)
    tool_version: str = __version__
    provenance: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    excluded: dict = field(default_factory=dict)

    @property
    def total_bytes(self) -> int:
        return sum(f["bytes"] for f in self.files.values())

    def record(self, entry: FileRecord) -> None:
        self.files[entry.key] = entry.as_dict()
        self.updated_at_utc = iso_utc()

    def as_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "generation": self.generation,
            "generation_note": (
                "Snapshot, not a delta. Every census result computed from "
                "these files must state this generation."
            ),
            "directory": self.directory,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "tool_version": self.tool_version,
            "provenance": self.provenance,
            "file_count": len(self.files),
            "total_bytes": self.total_bytes,
            "files": self.files,
            "excluded": self.excluded,
        }


def load_manifest(path: Path) -> Manifest | None:
    if not Path(path).is_file():
        return None
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    manifest = Manifest(
        generation=body.get("generation", ""),
        directory=body.get("directory", ""),
        created_at_utc=body.get("created_at_utc", iso_utc()),
        provenance=body.get("provenance", {}),
        excluded=body.get("excluded", {}),
    )
    manifest.files = body.get("files", {})
    return manifest


def save_manifest(manifest: Manifest, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest.as_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def free_space(path: Path) -> int:
    target = Path(path)
    while not target.exists() and target.parent != target:
        target = target.parent
    return shutil.disk_usage(target).free


def download(url: str, destination: Path, expected_size: int,
             etag: str | None = None, session=None, timeout: float = 300.0,
             on_progress=None) -> tuple:
    """Fetch one object, resuming a partial file if one is there.

    Returns (bytes_written, resumed). Raises IntegrityError if the finished
    file does not match what the listing promised.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    http = session or requests.Session()
    http.headers.setdefault("User-Agent", USER_AGENT)

    already = partial.stat().st_size if partial.exists() else 0
    if already > expected_size:
        # A stale part file from a different generation; start over.
        partial.unlink()
        already = 0

    resumed = already > 0
    headers = {"Range": f"bytes={already}-"} if already else {}

    if already < expected_size:
        with http.get(url, headers=headers, stream=True, timeout=timeout) as response:
            if already and response.status_code != 206:
                # The server ignored the range; restart cleanly.
                already, resumed = 0, False
                partial.unlink(missing_ok=True)
            response.raise_for_status()
            mode = "ab" if already else "wb"
            with open(partial, mode) as handle:
                for block in response.iter_content(CHUNK):
                    if not block:
                        continue
                    handle.write(block)
                    already += len(block)
                    if on_progress:
                        on_progress(already, expected_size)
                handle.flush()
                os.fsync(handle.fileno())

    actual = partial.stat().st_size
    if actual != expected_size:
        raise IntegrityError(
            f"{destination.name}: expected {expected_size} bytes, got {actual}"
        )

    if etag and "-" not in etag and len(etag) == 32:
        if md5_file(partial) != etag:
            partial.unlink(missing_ok=True)
            raise IntegrityError(f"{destination.name}: ETag mismatch, file discarded")

    os.replace(partial, destination)
    return actual, resumed


def verify(path: Path, record: dict) -> list:
    """Re-check a downloaded file against its manifest entry."""
    problems = []
    path = Path(path)
    if not path.is_file():
        return [f"missing: {path.name}"]
    size = path.stat().st_size
    if size != record.get("bytes"):
        problems.append(f"{path.name}: size {size} != manifest {record.get('bytes')}")
    elif sha256_file(path) != record.get("sha256"):
        problems.append(f"{path.name}: sha256 mismatch")
    return problems
