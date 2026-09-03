"""Wayback Machine submission.

Uses the public Save Page Now endpoint. No credentials are required and none
are used. A Wayback failure never fails a capture: the local snapshot is the
primary record and the third-party copy is corroboration.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import quote, urljoin

import requests

from . import USER_AGENT

SAVE_ENDPOINT = "https://web.archive.org/save/"
AVAILABILITY_ENDPOINT = "https://archive.org/wayback/available"
WAYBACK_BASE = "https://web.archive.org"
SNAPSHOT_RE = re.compile(r"/web/(\d{14})(?:\w{2,3}_)?/")


@dataclass(frozen=True)
class WaybackResult:
    status: str  # submitted | existing | failed | skipped | disabled
    url: str | None = None
    timestamp: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _snapshot_from(candidate: str | None) -> tuple[str, str] | None:
    if not candidate:
        return None
    match = SNAPSHOT_RE.search(candidate)
    if not match:
        return None
    absolute = candidate if candidate.startswith("http") else urljoin(WAYBACK_BASE, candidate)
    return absolute, match.group(1)


def lookup(url: str, timeout: float = 30.0, session: requests.Session | None = None) -> WaybackResult:
    """Ask the availability API for the most recent existing snapshot."""
    http = session or requests
    try:
        response = http.get(
            AVAILABILITY_ENDPOINT,
            params={"url": url},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        response.raise_for_status()
        closest = (response.json().get("archived_snapshots") or {}).get("closest") or {}
    except (requests.RequestException, ValueError) as exc:
        return WaybackResult(status="failed", detail=f"availability lookup: {exc}")

    if not closest.get("available") or not closest.get("url"):
        return WaybackResult(status="failed", detail="no snapshot available")
    return WaybackResult(
        status="existing",
        url=closest["url"],
        timestamp=closest.get("timestamp"),
        detail="pre-existing snapshot; save request did not confirm a new one",
    )


def submit(url: str, timeout: float = 90.0) -> WaybackResult:
    """Submit a URL to Save Page Now and resolve the resulting snapshot URL."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})

    try:
        response = session.get(
            SAVE_ENDPOINT + quote(url, safe="/:?=&%~#@!$'()*+,;[]"),
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        fallback = lookup(url, timeout=min(timeout, 30.0), session=session)
        if fallback.status == "existing":
            return WaybackResult(
                status="existing",
                url=fallback.url,
                timestamp=fallback.timestamp,
                detail=f"save request failed ({exc.__class__.__name__}); reporting existing snapshot",
            )
        return WaybackResult(status="failed", detail=f"save request failed: {exc}")

    for candidate in (
        response.headers.get("Content-Location"),
        response.headers.get("Location"),
        response.url,
    ):
        found = _snapshot_from(candidate)
        if found:
            snapshot_url, timestamp = found
            return WaybackResult(status="submitted", url=snapshot_url, timestamp=timestamp)

    fallback = lookup(url, timeout=min(timeout, 30.0), session=session)
    if fallback.status == "existing":
        return WaybackResult(
            status="existing",
            url=fallback.url,
            timestamp=fallback.timestamp,
            detail=f"save returned HTTP {response.status_code} without a snapshot URL; "
                   "reporting most recent existing snapshot",
        )
    return WaybackResult(
        status="failed",
        detail=f"save returned HTTP {response.status_code} and no snapshot could be resolved",
    )
