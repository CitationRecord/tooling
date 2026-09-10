"""Command line: count a reporter pair across a bulk generation.

    python -m parallel pair --reporter "Cal. Rptr. 3d" --against "Cal. App. 5th"

Writes a JSON artifact naming the exact file it read, by sha256, so the figure
can be recomputed against the same bytes rather than against whatever the
bucket holds later.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bulk import DEFAULT_GENERATION
from bulk.config import default_directory

from . import ARTIFACT_KIND, SCHEMA, __version__
from .pairs import count_pair

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = REPO_ROOT / "results"

#: Stated in every artifact. The first two are the reason this package exists.
CAVEATS = [
    "This counts citations, not cases. A cluster carrying no citation in a "
    "reporter is not an absent case: the same decision may be held under the "
    "parallel reporter. Nothing here establishes that any case is missing "
    "from CourtListener.",
    "The share of one reporter's clusters carrying no citation in the other "
    "is not a coverage gap. It is how citations are distributed between two "
    "reporters. Reading it as a gap is the error this artifact exists to "
    "make checkable.",
    "Exact over the generation named, not sampled. It describes that bulk "
    "drop and is superseded by any later one.",
    "Reporter names are matched exactly as the bulk table stores them. A zero "
    "count means the string did not appear, which is more often a misspelling "
    "than an absence.",
    "Clusters are CourtListener's unit of decision. Where it holds duplicate "
    "records of one decision, that decision counts more than once.",
]


def _out(message: str = "") -> None:
    print(message, file=sys.stdout, flush=True)


def _note(message: str = "") -> None:
    print(message, file=sys.stderr, flush=True)


def iso_utc() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def _provenance() -> dict:
    """Run provenance, minus the fields that describe an API resolver."""
    from resolve.journal import run_provenance

    base = run_provenance()
    for key in ("api", "eyecite_version", "reporters_db_version",
                "resolver_version", "user_agent"):
        base.pop(key, None)
    base["parallel_version"] = __version__
    return base


def _source(directory: Path, generation: str) -> dict:
    """Describe the citations file, from the manifest where one exists."""
    manifest_path = directory / "manifest.json"
    entry: dict = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = (manifest.get("files") or {}).get("citations") or {}
        except (json.JSONDecodeError, OSError):
            entry = {}
    return {
        "file": entry.get("path") or f"citations-{generation}.csv.bz2",
        "sha256": entry.get("sha256"),
        "rows_declared": entry.get("rows"),
        "downloaded_at_utc": entry.get("downloaded_at_utc"),
        "manifest": str(manifest_path) if manifest_path.is_file() else None,
    }


def _document(args, result, source: dict) -> dict:
    return {
        "schema": SCHEMA,
        "artifact_kind": ARTIFACT_KIND,
        "generation": args.generation,
        "source": source,
        "pair": {"a": result.reporter_a, "b": result.reporter_b},
        "clusters": {
            "a_only": result.clusters_a_only,
            "b_only": result.clusters_b_only,
            "both": result.clusters_both,
            "a_total": result.clusters_a,
            "b_total": result.clusters_b,
            "either": result.clusters_either,
        },
        "citation_rows": {
            "a": result.rows_a,
            "b": result.rows_b,
            "scanned": result.rows_scanned,
            "without_cluster": result.rows_without_cluster,
        },
        "citation_types": {"a": result.types_a, "b": result.types_b},
        "arithmetic": {
            "a_only_share_of_a": result.a_only_share_of_a,
            "b_only_share_of_b": result.b_only_share_of_b,
        },
        "caveats": CAVEATS,
        "measured_at_utc": iso_utc(),
        "provenance": _provenance(),
    }


def _report(result, generation: str) -> None:
    a, b = result.reporter_a, result.reporter_b
    rows = [
        (f"{a}, no {b}", result.clusters_a_only),
        ("both", result.clusters_both),
        (f"{b}, no {a}", result.clusters_b_only),
        ("carrying either", result.clusters_either),
    ]
    width = max(len(label) for label, _ in rows)
    _out("")
    _out(f"{a} and {b}, generation {generation}")
    _out("")
    for label, count in rows:
        _out(f"  {label:<{width}}  {count:>10,}")
    _out("")
    _out(f"  {'citation rows scanned':<{width}}  {result.rows_scanned:>10,}")
    _out("")
    share = result.a_only_share_of_a
    _out(f"  {share:.1%} of {a} clusters carry no {b} citation. That is not a")
    _out(f"  coverage gap. Those {result.clusters_a_only:,} decisions are held; they are held")
    _out("  under one reporter name and not the other.")


def _write(document: dict, destination: Path) -> None:
    """Write via a temp file and replace, so a kill cannot truncate it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def cmd_pair(args) -> int:
    directory = Path(args.dir) if args.dir else default_directory(args.generation)
    source = _source(directory, args.generation)
    path = directory / source["file"]
    if not path.is_file():
        _note(f"no citations file at {path}")
        _note("fetch the generation first: python -m bulk fetch --only citations")
        return 2

    _note(f"reading   {path}")
    _note(f"pair      {args.reporter}  against  {args.against}")

    def progress(rows: int) -> None:
        _note(f"  scanned {rows:,} rows")

    result = count_pair(path, args.reporter, args.against,
                        on_progress=None if args.quiet else progress)

    if result.clusters_a == 0 or result.clusters_b == 0:
        missing = args.reporter if result.clusters_a == 0 else args.against
        _note("")
        _note(f"'{missing}' matched no citation row. Reporter names are matched "
              "exactly as stored; check the spelling before believing a zero.")
        return 1

    document = _document(args, result, source)
    _report(result, args.generation)

    destination = Path(args.out) if args.out else DEFAULT_RESULTS_DIR / (
        f"parallel-{_slug(result.reporter_a)}-vs-"
        f"{_slug(result.reporter_b)}-{args.generation}.json")
    _write(document, destination)
    _out("")
    _out(f"  written to {destination}")

    if args.json:
        _out(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parallel",
        description="Count clusters by the reporters their citations carry.")
    parser.add_argument("--version", action="version",
                        version=f"parallel {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    pair = subparsers.add_parser(
        "pair", help="count one reporter pair across a bulk generation")
    pair.add_argument("--reporter", required=True, metavar="NAME",
                      help="reporter A, exactly as stored")
    pair.add_argument("--against", required=True, metavar="NAME",
                      help="reporter B, exactly as stored")
    pair.add_argument("--generation", default=DEFAULT_GENERATION,
                      metavar="YYYY-MM-DD")
    pair.add_argument("--dir", metavar="PATH",
                      help="bulk data directory (default: the generation's)")
    pair.add_argument("--out", metavar="FILE",
                      help=f"artifact path (default: under {DEFAULT_RESULTS_DIR})")
    pair.add_argument("--json", action="store_true", help="also emit the artifact")
    pair.add_argument("--quiet", action="store_true", help="no progress on stderr")
    pair.set_defaults(func=cmd_pair)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
