"""Capture run: robots check, render, hash, store, submit, append."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import SCHEMA
from .claims import Claim, ClaimSet
from .config import Config
from .fetch import Browser, FetchError, PageCapture
from .fetch import browser as browser_session
from .manifest import append as append_record
from .manifest import last_capture_by_claim, write_once
from .provenance import iso_utc, run_provenance, stamp_utc, utc_now
from .robots import RobotsCache
from .wayback import WaybackResult
from .wayback import submit as wayback_submit

STATUS_CAPTURED = "captured"
STATUS_BLOCKED = "blocked_by_robots"
STATUS_FETCH_ERROR = "fetch_error"
STATUS_UNVERIFIED = "unverified_source"

CHANGE_FIRST = "first_capture"
CHANGE_SAME = "unchanged"
CHANGE_DIFFERENT = "changed"
CHANGE_NONE = "not_compared"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class RunSummary:
    captured: int = 0
    changed: int = 0
    unchanged: int = 0
    first: int = 0
    blocked: int = 0
    unverified: int = 0
    failed: int = 0
    wayback_ok: int = 0
    records: list = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.failed or self.blocked or self.unverified:
            return 1
        if self.changed:
            return 2
        return 0


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _change_block(claim: Claim, previous, content_hash: str, text_hash: str) -> dict:
    if previous is None:
        return {"content": CHANGE_FIRST, "text": CHANGE_FIRST}

    previous_content = previous.get("content_sha256")
    previous_text = previous.get("text_sha256")
    if not previous_text:
        text_change = CHANGE_FIRST
    elif previous_text == text_hash:
        text_change = CHANGE_SAME
    else:
        text_change = CHANGE_DIFFERENT

    block = {
        "content": CHANGE_SAME if previous_content == content_hash else CHANGE_DIFFERENT,
        "text": text_change,
        "compared_to_record_id": previous.get("record_id"),
        "compared_to_captured_at_utc": previous.get("captured_at_utc"),
        "previous_content_sha256": previous_content,
        "previous_text_sha256": previous_text,
    }
    if previous.get("url") and previous["url"] != claim.url:
        # A hash comparison across two different URLs says nothing useful.
        block["url_changed_since"] = previous["url"]
    return block


def _base_record(claim: Claim, provenance: dict, moment) -> dict:
    return {
        "schema": SCHEMA,
        "record_id": str(uuid.uuid4()),
        "claim_id": claim.id,
        "captured_at_utc": iso_utc(moment),
        "url": claim.url,
        "verified_source": claim.verified,
        "claim_text": claim.claim_text,
        "claim_text_sha256": claim.claim_text_sha256,
        "system": claim.system,
        "vendor": claim.vendor,
        "profile_ref": claim.profile_ref,
        "notes": claim.notes,
        "provenance": provenance,
    }


def capture_claim(
    claim: Claim,
    config: Config,
    browser: Browser,
    robots: RobotsCache,
    provenance: dict,
    previous,
) -> dict:
    """Capture one claim and return the manifest record, not yet appended."""
    moment = utc_now()
    record = _base_record(claim, provenance, moment)

    if not claim.capturable:
        # The URL has not been confirmed as the primary source. Nothing is
        # requested at all, not even robots.txt: a wrong source in the
        # evidence archive is worse than a missing one.
        reason = (
            "url is not set"
            if not claim.url
            else "source URL is not confirmed (verified: false)"
        )
        record.update(
            status=STATUS_UNVERIFIED,
            content_sha256=None,
            error=f"not fetched: {reason}",
            change={"content": CHANGE_NONE, "text": CHANGE_NONE},
            robots={"checked": False, "reason": "not checked: source URL is unconfirmed"},
            wayback={
                "status": "skipped",
                "url": None,
                "timestamp": None,
                "detail": "not submitted: source URL is unconfirmed",
            },
        )
        return record

    verdict = robots.check(claim.url)
    record["robots"] = {
        "robots_url": verdict.robots_url,
        "allowed": verdict.allowed,
        "reason": verdict.reason,
        "http_status": verdict.http_status,
        "crawl_delay": verdict.crawl_delay,
    }

    if not verdict.allowed:
        # No fetch, no screenshot, no third-party save request. The refusal
        # itself is the record.
        record.update(
            status=STATUS_BLOCKED,
            content_sha256=None,
            error=verdict.reason,
            change={"content": CHANGE_NONE, "text": CHANGE_NONE},
            wayback={
                "status": "skipped",
                "url": None,
                "timestamp": None,
                "detail": "not submitted: disallowed by robots.txt",
            },
        )
        return record

    try:
        page = browser.capture(
            url=claim.url,
            wait_until=claim.wait_until or config.wait_until,
            settle_ms=config.settle_ms if claim.settle_ms is None else claim.settle_ms,
            full_page=config.full_page if claim.full_page is None else claim.full_page,
        )
    except FetchError as exc:
        record.update(
            status=STATUS_FETCH_ERROR,
            content_sha256=None,
            error=str(exc),
            change={"content": CHANGE_NONE, "text": CHANGE_NONE},
            wayback={
                "status": "skipped",
                "url": None,
                "timestamp": None,
                "detail": "not submitted: page could not be captured",
            },
        )
        return record

    content_hash = sha256_text(page.html)
    text_hash = sha256_text(page.text)
    paths = _store(claim, page, config, moment)

    record.update(
        status=STATUS_CAPTURED,
        final_url=page.final_url,
        http_status=page.http_status,
        content_type=page.content_type,
        page_title=page.title,
        content_sha256=content_hash,
        content_bytes=len(page.html.encode("utf-8")),
        text_sha256=text_hash,
        text_bytes=len(page.text.encode("utf-8")),
        screenshot_sha256=hashlib.sha256(page.screenshot_png).hexdigest(),
        screenshot_full_page=page.screenshot_full_page,
        paths=paths,
        change=_change_block(claim, previous, content_hash, text_hash),
        error=None,
    )

    if config.submit_wayback:
        result = wayback_submit(claim.url, timeout=config.wayback_timeout)
    else:
        result = WaybackResult(status="disabled", detail="--no-wayback")
    record["wayback"] = result.as_dict()

    _write_sidecar(config, paths, record)
    return record


def _store(claim: Claim, page: PageCapture, config: Config, moment) -> dict:
    """Write the snapshot files. Written once, then read-only."""
    directory = config.snapshot_root / claim.id / stamp_utc(moment)
    if directory.exists():
        directory = directory.with_name(directory.name + "-" + uuid.uuid4().hex[:6])

    html_path = write_once(directory / "page.html", page.html.encode("utf-8"))
    png_path = write_once(directory / "page.png", page.screenshot_png)
    text_path = write_once(directory / "page.txt", page.text.encode("utf-8"))

    return {
        "html": _relative(html_path, config.out_dir),
        "screenshot": _relative(png_path, config.out_dir),
        "text": _relative(text_path, config.out_dir),
    }


def _write_sidecar(config: Config, paths: dict, record: dict) -> None:
    """A copy of the record beside the snapshot, so the directory stands alone."""
    directory = (config.out_dir / paths["html"]).parent
    body = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        write_once(directory / "record.json", body.encode("utf-8"))
    except OSError:
        pass


class _NoBrowser:
    """Stands in when no selected claim may be fetched."""

    version = None

    def capture(self, *args, **kwargs):
        raise FetchError("no browser was started: nothing in this run is capturable")


@contextmanager
def _no_browser():
    yield _NoBrowser()


def run(config: Config, claim_set: ClaimSet, on_event=None) -> RunSummary:
    """Capture every selected claim, appending each record as it completes."""
    emit = on_event or (lambda *args, **kwargs: None)
    claims = claim_set.select(config.only)
    summary = RunSummary()

    config.out_dir.mkdir(parents=True, exist_ok=True)
    previous_by_claim = last_capture_by_claim(config.manifest_path)
    robots = RobotsCache(timeout=config.robots_timeout)

    # No browser when there is nothing to fetch. A run of nothing but
    # unconfirmed sources must still record its refusals, so it cannot depend
    # on Chromium being installed.
    session = browser_session(config) if any(c.capturable for c in claims) else _no_browser()

    with session as browser:
        provenance = run_provenance(claim_set, browser_version=browser.version)
        last_request_at = {}

        for index, claim in enumerate(claims):
            emit("start", claim=claim, index=index, total=len(claims))
            if claim.capturable:
                _throttle(claim, config, robots, last_request_at)

            record = capture_claim(
                claim=claim,
                config=config,
                browser=browser,
                robots=robots,
                provenance=provenance,
                previous=previous_by_claim.get(claim.id),
            )
            if claim.capturable:
                last_request_at[claim.origin] = time.monotonic()

            append_record(config.manifest_path, record)
            _tally(summary, record)
            summary.records.append(record)
            emit("done", claim=claim, record=record)

    return summary


def _throttle(claim: Claim, config: Config, robots: RobotsCache, last: dict) -> None:
    """Wait out the greater of the configured delay and any Crawl-delay."""
    previous = last.get(claim.origin)
    if previous is None:
        return
    delay = config.min_delay
    crawl_delay = robots.check(claim.url).crawl_delay
    if crawl_delay:
        delay = max(delay, crawl_delay)
    remaining = delay - (time.monotonic() - previous)
    if remaining > 0:
        time.sleep(remaining)


def _tally(summary: RunSummary, record: dict) -> None:
    status = record.get("status")
    if status == STATUS_UNVERIFIED:
        summary.unverified += 1
        return
    if status == STATUS_BLOCKED:
        summary.blocked += 1
        return
    if status != STATUS_CAPTURED:
        summary.failed += 1
        return

    summary.captured += 1
    change = (record.get("change") or {}).get("content")
    if change == CHANGE_FIRST:
        summary.first += 1
    elif change == CHANGE_DIFFERENT:
        summary.changed += 1
    else:
        summary.unchanged += 1
    if (record.get("wayback") or {}).get("status") in {"submitted", "existing"}:
        summary.wayback_ok += 1
