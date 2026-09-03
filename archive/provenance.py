"""Run provenance.

Design constraint: a result without provenance is not a result. Every record
carries the tooling commit, the archiver version, the browser build, the
identity the request was made under, and the hash of the claim file it came
from.
"""

from __future__ import annotations

import importlib.metadata
import platform
import subprocess
import sys
from datetime import datetime, timezone

from . import USER_AGENT, __version__
from .config import REPO_ROOT


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """RFC 3339 with an explicit Z. Timestamps are always UTC."""
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def stamp_utc(moment: datetime) -> str:
    """Filesystem-safe UTC stamp used for snapshot directory names."""
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def tooling_commit() -> dict:
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    return {
        "commit": commit,
        "dirty": bool(status) if status is not None else None,
    }


def run_provenance(claim_set, browser_version: str | None = None) -> dict:
    """Provenance shared by every record in one capture run."""
    try:
        playwright_version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        playwright_version = None

    return {
        "archiver_version": __version__,
        "tooling": tooling_commit(),
        "methodology_version": claim_set.methodology_version,
        "edition": claim_set.edition,
        "claims_file": claim_set.path.name,
        "claims_file_sha256": claim_set.file_sha256,
        "user_agent": USER_AGENT,
        "browser": browser_version,
        "playwright_version": playwright_version,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }
