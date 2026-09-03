"""Command line interface for the claim archiver.

    py -m archive capture      snapshot every claim, append a record each
    py -m archive status       last known state of each claim
    py -m archive changes      records where the page hash moved
    py -m archive verify       recompute stored hashes against the manifest
    py -m archive validate     parse the claim file and check robots.txt

Exit codes: 0 clean, 1 something failed or was blocked, 2 changes detected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import USER_AGENT, __version__
from . import capture as capture_mod
from .claims import ClaimsError, load_claims
from .config import DEFAULT_CLAIMS_PATH, DEFAULT_OUT_DIR, Config
from .fetch import FetchError
from .manifest import last_record_by_claim, read
from .robots import RobotsCache

SHORT = 12


def _out(message: str = "") -> None:
    print(message, flush=True)


def _short(digest) -> str:
    return digest[:SHORT] if isinstance(digest, str) else "-"


def _build_config(args, **overrides) -> Config:
    base = Config(
        claims_path=Path(getattr(args, "claims", DEFAULT_CLAIMS_PATH)),
        out_dir=Path(args.out),
        only=tuple(getattr(args, "only", ()) or ()),
        submit_wayback=not getattr(args, "no_wayback", False),
        page_timeout_ms=getattr(args, "timeout", 45_000),
        min_delay=getattr(args, "delay", 2.0),
        settle_ms=getattr(args, "settle", 1_500),
        headless=not getattr(args, "headed", False),
        dry_run=getattr(args, "dry_run", False),
        **overrides,
    )
    return base.resolved()


# --------------------------------------------------------------------------
# capture


def _on_event(event: str, **kwargs) -> None:
    if event == "start":
        claim = kwargs["claim"]
        _out(f"[{kwargs['index'] + 1}/{kwargs['total']}] {claim.id}  {claim.url or '(no url)'}")
        return

    record = kwargs["record"]
    status = record.get("status")
    if status == capture_mod.STATUS_UNVERIFIED:
        _out(f"    skipped   {record.get('error')}")
        return
    if status == capture_mod.STATUS_BLOCKED:
        _out(f"    blocked   {record.get('error')}")
        return
    if status != capture_mod.STATUS_CAPTURED:
        _out(f"    FAILED    {record.get('error')}")
        return

    change = (record.get("change") or {}).get("content")
    text_change = (record.get("change") or {}).get("text")
    label = {
        capture_mod.CHANGE_FIRST: "first capture",
        capture_mod.CHANGE_SAME: "unchanged",
        capture_mod.CHANGE_DIFFERENT: "CHANGED",
    }.get(change, str(change))

    _out(f"    captured  http {record.get('http_status')}  sha256 {_short(record.get('content_sha256'))}  {label}")
    if change == capture_mod.CHANGE_DIFFERENT:
        previous = (record.get("change") or {}).get("previous_content_sha256")
        visible = "visible text also changed" if text_change == capture_mod.CHANGE_DIFFERENT else "visible text unchanged"
        _out(f"              was {_short(previous)}  ({visible})")
    wayback = record.get("wayback") or {}
    _out(f"    wayback   {wayback.get('status')}  {wayback.get('url') or wayback.get('detail') or ''}".rstrip())


def cmd_capture(args) -> int:
    config = _build_config(args)
    try:
        claim_set = load_claims(config.claims_path)
    except ClaimsError as exc:
        _out(f"error: {exc}")
        return 1

    try:
        selected = claim_set.select(config.only)
    except ClaimsError as exc:
        _out(f"error: {exc}")
        return 1

    _out(f"claims     {config.claims_path}  ({len(selected)} of {len(claim_set.claims)} selected)")
    _out(f"output     {config.out_dir}")
    _out(f"identity   {USER_AGENT}")
    _out(f"wayback    {'enabled' if config.submit_wayback else 'disabled'}")
    unverified = [c for c in selected if not c.capturable]
    if unverified:
        _out(
            f"unverified {len(unverified)} entr{'y' if len(unverified) == 1 else 'ies'} "
            f"will be skipped: {', '.join(c.id for c in unverified)}"
        )
    _out()

    if config.dry_run:
        return _dry_run(config, selected)

    try:
        summary = capture_mod.run(config, claim_set, on_event=_on_event)
    except FetchError as exc:
        _out(f"error: {exc}")
        return 1

    _out()
    _out(
        f"captured {summary.captured}  "
        f"(first {summary.first}, unchanged {summary.unchanged}, changed {summary.changed})  "
        f"blocked {summary.blocked}  unverified {summary.unverified}  "
        f"failed {summary.failed}  wayback {summary.wayback_ok}"
    )
    _out(f"manifest   {config.manifest_path}")

    if args.json:
        _out(json.dumps({"records": summary.records}, ensure_ascii=False, indent=2, sort_keys=True))
    return summary.exit_code


def _dry_run(config: Config, claims) -> int:
    """Validate and check robots.txt without fetching or writing anything."""
    robots = RobotsCache(timeout=config.robots_timeout)
    blocked = skipped = 0
    for claim in claims:
        if not claim.capturable:
            # Not even a robots.txt request: the origin itself is unconfirmed.
            skipped += 1
            _out(f"{claim.id}\n    {claim.url or '(no url)'}\n    skipped: source URL is unconfirmed")
            continue
        verdict = robots.check(claim.url)
        if not verdict.allowed:
            blocked += 1
        delay = f"  crawl-delay {verdict.crawl_delay}s" if verdict.crawl_delay else ""
        _out(f"{claim.id}\n    {claim.url}\n    robots: {verdict.reason}{delay}")
    _out()
    _out(
        f"dry run: nothing fetched, nothing written. {len(claims)} claim(s), "
        f"{blocked} blocked, {skipped} unverified."
    )
    return 1 if (blocked or skipped) else 0


# --------------------------------------------------------------------------
# status / changes / verify / validate


def cmd_status(args) -> int:
    config = _build_config(args)
    latest = last_record_by_claim(config.manifest_path)
    if not latest:
        _out(f"no records in {config.manifest_path}")
        return 0

    if args.json:
        _out(json.dumps(latest, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    _out(f"{'claim':<28} {'captured (UTC)':<22} {'status':<18} {'sha256':<14} change")
    _out("-" * 100)
    changed = 0
    for claim_id in sorted(latest):
        record = latest[claim_id]
        change = (record.get("change") or {}).get("content", "-")
        if change == capture_mod.CHANGE_DIFFERENT:
            changed += 1
        _out(
            f"{claim_id:<28} {str(record.get('captured_at_utc'))[:19]:<22} "
            f"{str(record.get('status')):<18} {_short(record.get('content_sha256')):<14} {change}"
        )
    _out()
    _out(f"{len(latest)} claim(s) tracked, {changed} changed at last capture")
    return 2 if changed else 0


def cmd_changes(args) -> int:
    config = _build_config(args)
    hits = []
    for entry in read(config.manifest_path):
        record = entry.record
        if (record.get("change") or {}).get("content") != capture_mod.CHANGE_DIFFERENT:
            continue
        if args.since and str(record.get("captured_at_utc", "")) < args.since:
            continue
        hits.append(record)

    if args.json:
        _out(json.dumps(hits, ensure_ascii=False, indent=2, sort_keys=True))
        return 2 if hits else 0

    if not hits:
        _out("no hash changes recorded" + (f" since {args.since}" if args.since else ""))
        return 0

    for record in hits:
        change = record.get("change") or {}
        _out(f"{record.get('claim_id')}  {record.get('captured_at_utc')}")
        _out(f"    url          {record.get('url')}")
        _out(f"    tracked      {record.get('claim_text')}")
        _out(f"    html hash    {_short(change.get('previous_content_sha256'))} -> {_short(record.get('content_sha256'))}")
        _out(f"    visible text {change.get('text')}")
        _out(f"    snapshot     {(record.get('paths') or {}).get('html')}")
        if change.get("url_changed_since"):
            _out(f"    note         url changed from {change['url_changed_since']}")
        _out()
    _out(f"{len(hits)} change record(s)")
    return 2


def cmd_verify(args) -> int:
    """Recompute the hash of every stored snapshot and compare to the manifest."""
    config = _build_config(args)
    checked = missing = mismatched = 0

    for entry in read(config.manifest_path):
        record = entry.record
        paths = record.get("paths") or {}
        expected = record.get("content_sha256")
        if not expected or not paths.get("html"):
            continue

        checked += 1
        problems = []
        html_path = config.out_dir / paths["html"]
        if not html_path.is_file():
            problems.append(f"missing {paths['html']}")
        else:
            actual = hashlib.sha256(html_path.read_bytes()).hexdigest()
            if actual != expected:
                problems.append(f"html hash {_short(actual)} != manifest {_short(expected)}")

        png_path = config.out_dir / paths.get("screenshot", "")
        expected_png = record.get("screenshot_sha256")
        if paths.get("screenshot"):
            if not png_path.is_file():
                problems.append(f"missing {paths['screenshot']}")
            elif expected_png:
                actual_png = hashlib.sha256(png_path.read_bytes()).hexdigest()
                if actual_png != expected_png:
                    problems.append("screenshot hash mismatch")

        if problems:
            if any(p.startswith("missing") for p in problems):
                missing += 1
            else:
                mismatched += 1
            _out(f"line {entry.line_no}  {record.get('claim_id')}  {record.get('captured_at_utc')}")
            for problem in problems:
                _out(f"    {problem}")

    _out()
    _out(f"verified {checked} record(s): {missing} with missing files, {mismatched} with hash mismatches")
    return 1 if (missing or mismatched) else 0


def cmd_validate(args) -> int:
    try:
        claim_set = load_claims(Path(args.claims).expanduser().resolve())
    except ClaimsError as exc:
        _out(f"error: {exc}")
        return 1

    _out(f"{claim_set.path}")
    _out(f"sha256 {claim_set.file_sha256}")
    _out(f"{len(claim_set.claims)} claim(s), methodology version {claim_set.methodology_version or 'unset'}")
    _out()
    for claim in claim_set.claims:
        mark = "verified  " if claim.capturable else "UNVERIFIED"
        _out(f"  {mark}  {claim.id:<32} {claim.url or '(no url)'}")

    unverified = [c for c in claim_set.claims if not c.capturable]
    if unverified:
        _out()
        _out(
            f"{len(unverified)} entr{'y' if len(unverified) == 1 else 'ies'} will not be "
            "fetched until the source URL is confirmed and verified is set to true"
        )
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archive",
        description="Snapshot, hash, and log vendor claims for the Citation Record.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"archive {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def add_common(sub):
        sub.add_argument(
            "--out", default=str(DEFAULT_OUT_DIR),
            metavar="DIR",
            help=f"archive directory, outside this repo (default: {DEFAULT_OUT_DIR})",
        )
        sub.add_argument("--json", action="store_true", help="also emit JSON")

    capture_parser = subparsers.add_parser("capture", help="snapshot every claim")
    add_common(capture_parser)
    capture_parser.add_argument(
        "--claims", default=str(DEFAULT_CLAIMS_PATH), metavar="PATH",
        help=f"claim list (default: {DEFAULT_CLAIMS_PATH})",
    )
    capture_parser.add_argument(
        "--only", action="append", metavar="ID",
        help="capture only this claim id; repeatable",
    )
    capture_parser.add_argument("--no-wayback", action="store_true", help="skip Wayback submission")
    capture_parser.add_argument("--timeout", type=int, default=45_000, metavar="MS", help="page load timeout")
    capture_parser.add_argument("--settle", type=int, default=1_500, metavar="MS", help="pause after load before capture")
    capture_parser.add_argument("--delay", type=float, default=2.0, metavar="SEC", help="minimum delay between requests to one origin")
    capture_parser.add_argument("--headed", action="store_true", help="run the browser headed, for debugging")
    capture_parser.add_argument("--dry-run", action="store_true", help="validate and check robots.txt only")
    capture_parser.set_defaults(func=cmd_capture)

    status_parser = subparsers.add_parser("status", help="last known state of each claim")
    add_common(status_parser)
    status_parser.set_defaults(func=cmd_status)

    changes_parser = subparsers.add_parser("changes", help="records where the page hash moved")
    add_common(changes_parser)
    changes_parser.add_argument("--since", metavar="UTC", help="only records on or after this ISO 8601 timestamp")
    changes_parser.set_defaults(func=cmd_changes)

    verify_parser = subparsers.add_parser("verify", help="recompute stored hashes against the manifest")
    add_common(verify_parser)
    verify_parser.set_defaults(func=cmd_verify)

    validate_parser = subparsers.add_parser("validate", help="parse and check the claim file")
    validate_parser.add_argument("--claims", default=str(DEFAULT_CLAIMS_PATH), metavar="PATH")
    validate_parser.set_defaults(func=cmd_validate)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _out("\ninterrupted; records already appended are intact")
        return 130


if __name__ == "__main__":
    sys.exit(main())
