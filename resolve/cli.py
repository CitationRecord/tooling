"""Command line interface for the citation resolver.

    py -m resolve cite "576 U.S. 644"      resolve one or more citations
    py -m resolve batch citations.txt      resolve a file, one per line
    py -m resolve log                      summarise the lookup journal
    py -m resolve cache --stats            inspect or clear the cache

Exit codes: 0 every citation resolved, 1 a lookup errored, 2 at least one
citation was not found in CourtListener. Coverage gaps alone exit 0, because
they are not findings against a system.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import TOKEN_ENV_VAR, USER_AGENT, __version__
from .cache import Cache
from .client import CourtListener, MissingToken, load_env, redact
from .config import DEFAULT_OUT_DIR, REPO_ROOT, Config
from .journal import Journal, run_provenance
from .models import (
    STATUS_AMBIGUOUS,
    STATUS_ERROR,
    STATUS_NOT_COVERED,
    STATUS_NOT_FOUND,
    STATUS_RESOLVED,
)
from .resolver import Resolver

LABEL = {
    STATUS_RESOLVED: "RESOLVED",
    STATUS_NOT_FOUND: "NOT FOUND",
    STATUS_NOT_COVERED: "NOT COVERED",
    STATUS_AMBIGUOUS: "AMBIGUOUS",
    STATUS_ERROR: "ERROR",
}


def _out(message: str = "") -> None:
    print(message, flush=True)


def _note(message: str = "") -> None:
    """Run context goes to stderr so --json leaves stdout pure JSON."""
    print(message, file=sys.stderr, flush=True)


def _config(args) -> Config:
    return Config(
        out_dir=Path(args.out),
        use_cache=not getattr(args, "no_cache", False),
        refresh=getattr(args, "refresh", False),
        probe_coverage=not getattr(args, "no_coverage_probe", False),
        negative_max_age_days=getattr(args, "max_age", 30),
    ).resolved()


def _print(resolution, verbose: bool) -> None:
    tag = LABEL.get(resolution.status, resolution.status.upper())
    source = " (cached)" if resolution.from_cache else ""
    _out(f"{tag:<12} {resolution.query}{source}")

    if resolution.status == STATUS_RESOLVED:
        _out(f"             {resolution.case_name}")
        _out(
            f"             {resolution.volume} {resolution.reporter} "
            f"{resolution.page}  ({resolution.year})  {resolution.court}"
        )
        history = resolution.subsequent_history or {}
        carried = [
            f"{key.replace('_', ' ')}: {history[key]}"
            for key in ("history", "disposition", "procedural_history", "other_dates")
            if history.get(key)
        ]
        _out(
            f"             status {history.get('precedential_status')}, "
            f"cited by {history.get('citation_count')} case(s)"
        )
        for line in carried:
            _out(f"             {line}")
        if not carried:
            _out("             no subsequent history recorded (see caveat in --json)")
        for discrepancy in resolution.discrepancies:
            _out(f"             ! {discrepancy['detail']}")
        if verbose and resolution.parallel_citations:
            _out(f"             parallel: {', '.join(resolution.parallel_citations)}")
        if resolution.courtlistener_url:
            _out(f"             {resolution.courtlistener_url}")
    else:
        _out(f"             {resolution.explanation}")
        if resolution.status == STATUS_AMBIGUOUS:
            for candidate in resolution.candidates[:5]:
                _out(f"             - {candidate['case_name']} ({candidate['date_filed']})")
    _out()


def _summarise(resolutions: list) -> dict:
    counts = {}
    for resolution in resolutions:
        counts[resolution.status] = counts.get(resolution.status, 0) + 1
    return counts


def _exit_code(counts: dict) -> int:
    if counts.get(STATUS_ERROR):
        return 1
    if counts.get(STATUS_NOT_FOUND):
        return 2
    return 0


def _run(args, queries: list) -> int:
    config = _config(args)
    load_env(REPO_ROOT / ".env")

    try:
        client = CourtListener(config)
    except MissingToken as exc:
        _note(f"error: {redact(str(exc))}")
        return 1

    config.out_dir.mkdir(parents=True, exist_ok=True)
    cache = Cache(config.cache_path, config.negative_max_age_days)
    journal = Journal(config.journal_path, run_provenance(getattr(args, "methodology", None)))

    emit = _note if args.json else _out
    emit(f"api        {config.api_base}")
    emit(f"token      {TOKEN_ENV_VAR} loaded from the environment")
    emit(f"identity   {USER_AGENT}")
    emit(f"cache      {config.cache_path}")
    emit(f"journal    {config.journal_path}")
    emit("")

    resolutions = []
    try:
        resolver = Resolver(client, config, cache=cache, journal=journal)
        for query in queries:
            resolution = resolver.resolve(query)
            resolutions.append(resolution)
            if not args.json:
                _print(resolution, args.verbose)
    finally:
        client.close()
        cache.close()

    counts = _summarise(resolutions)
    if args.json:
        _out(json.dumps([r.as_dict() for r in resolutions], indent=2, sort_keys=True))
    else:
        parts = [f"{LABEL[k].lower()} {counts.get(k, 0)}" for k in LABEL if counts.get(k)]
        _out("  ".join(parts) or "nothing resolved")
        if counts.get(STATUS_NOT_COVERED):
            _out(
                "note: not-covered citations are unscorable, not errors. "
                "They do not count toward Class 1."
            )
    return _exit_code(counts)


def cmd_cite(args) -> int:
    return _run(args, args.citation)


def read_batch(path: Path) -> list:
    """One citation per line, blanks and # comments dropped.

    Decoded as utf-8-sig: PowerShell's `Out-File -Encoding utf8` writes a byte
    order mark, which would otherwise ride along on the first citation and
    make it unparseable.
    """
    return [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def cmd_batch(args) -> int:
    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        _out(f"error: no such file: {path}")
        return 1
    queries = read_batch(path)
    if not queries:
        _out(f"error: no citations in {path}")
        return 1
    return _run(args, queries)


def cmd_log(args) -> int:
    config = _config(args)
    journal = Journal(config.journal_path, {})
    entries = list(journal.read())
    if not entries:
        _out(f"no lookups logged in {config.journal_path}")
        return 0

    if args.json:
        _out(json.dumps(entries, indent=2, sort_keys=True))
        return 0

    counts = {}
    cached = 0
    for entry in entries:
        counts[entry.get("status")] = counts.get(entry.get("status"), 0) + 1
        cached += 1 if entry.get("from_cache") else 0

    _out(f"{config.journal_path}")
    _out(f"{len(entries)} lookup(s) logged, {cached} served from cache")
    _out()
    for status, count in sorted(counts.items()):
        _out(f"  {str(status):<14} {count}")
    _out()
    _out(f"first  {entries[0].get('logged_at_utc')}")
    _out(f"last   {entries[-1].get('logged_at_utc')}")
    if args.tail:
        _out()
        for entry in entries[-args.tail:]:
            _out(
                f"  {entry.get('logged_at_utc')}  {str(entry.get('status')):<12} "
                f"{entry.get('query')}"
            )
    return 0


def cmd_cache(args) -> int:
    config = _config(args)
    with Cache(config.cache_path, config.negative_max_age_days) as cache:
        if args.clear:
            cache.clear()
            _out(f"cleared {config.cache_path}")
            return 0
        stats = cache.stats()
    _out(json.dumps(stats, indent=2, sort_keys=True) if args.json else _format_stats(stats))
    return 0


def _format_stats(stats: dict) -> str:
    lines = [
        stats["path"],
        f"{stats['resolutions']} cached resolution(s), "
        f"{stats['coverage_rows']} coverage row(s), "
        f"{stats['court_rows']} court name(s)",
        f"negative results expire after {stats['negative_max_age_days']} days; "
        "resolved cases do not expire",
        "",
    ]
    for status, count in sorted(stats["by_status"].items()):
        lines.append(f"  {status:<14} {count}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resolve",
        description="Resolve case citations against CourtListener.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"resolve {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def common(sub, lookup: bool = False):
        sub.add_argument("--out", default=str(DEFAULT_OUT_DIR), metavar="DIR",
                         help=f"cache and journal directory (default: {DEFAULT_OUT_DIR})")
        sub.add_argument("--json", action="store_true", help="emit JSON")
        sub.add_argument("--max-age", type=int, default=30, metavar="DAYS",
                         help="how long a negative result stays cached (default: 30)")
        if lookup:
            sub.add_argument("--verbose", "-v", action="store_true")
            sub.add_argument("--refresh", action="store_true",
                             help="ignore cached results and re-query")
            sub.add_argument("--no-cache", action="store_true", help="neither read nor write the cache")
            sub.add_argument("--no-coverage-probe", action="store_true",
                             help="skip the coverage probe; every miss becomes not_covered")
            sub.add_argument("--methodology", metavar="VERSION",
                             help="methodology version to record in the journal")

    cite_parser = subparsers.add_parser("cite", help="resolve one or more citations")
    cite_parser.add_argument("citation", nargs="+", help="citation text, quoted")
    common(cite_parser, lookup=True)
    cite_parser.set_defaults(func=cmd_cite)

    batch_parser = subparsers.add_parser("batch", help="resolve a file of citations")
    batch_parser.add_argument("file", help="one citation per line; # comments ignored")
    common(batch_parser, lookup=True)
    batch_parser.set_defaults(func=cmd_batch)

    log_parser = subparsers.add_parser("log", help="summarise the lookup journal")
    common(log_parser)
    log_parser.add_argument("--tail", type=int, default=0, metavar="N",
                            help="also print the last N lookups")
    log_parser.set_defaults(func=cmd_log)

    cache_parser = subparsers.add_parser("cache", help="inspect or clear the cache")
    common(cache_parser)
    cache_parser.add_argument("--stats", action="store_true", help="default action")
    cache_parser.add_argument("--clear", action="store_true", help="empty the cache")
    cache_parser.set_defaults(func=cmd_cache)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except MissingToken as exc:
        _out(f"error: {redact(str(exc))}")
        return 1
    except KeyboardInterrupt:
        _out("\ninterrupted; lookups already logged are intact")
        return 130


if __name__ == "__main__":
    sys.exit(main())
