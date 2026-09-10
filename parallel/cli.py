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
from census.frame import PROBE_FRAME, sample_volumes, strata

from . import ARTIFACT_KIND, RECONSTRUCTION_KIND, SCHEMA, VOLUMES_SCHEMA, __version__
from .pairs import count_pair
from .volumes import count_volumes

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


#: Stated in every volumes artifact. The first is why the command exists.
VOLUME_CAVEATS = [
    "Three measurements, not one. The zero rate among sampled volumes, the "
    "zero rate across the declared range, and the highest volume the corpus "
    "holds are separate quantities over separate populations. Reading the "
    "first as a corpus coverage share is the error this artifact corrects.",
    "A zero beyond the highest volume held is not a coverage gap. It is a "
    "volume the corpus does not reach, and it says nothing about how well the "
    "volumes it does reach are held.",
    "The declared volume range is a parameter of the sampling frame, not a "
    "fact read from CourtListener. It is deliberately wider than the corpus "
    "so that the corpus stopping short stays visible instead of being hidden "
    "by a range trimmed to fit.",
    "Exact over the generation named, not sampled. It describes that bulk "
    "drop and is superseded by any later one.",
    "This counts citations, not cases. A volume holding no citation in this "
    "reporter may still have its decisions held under a parallel reporter.",
]

#: Why this artifact exists at all, carried inside it.
RECONSTRUCTION = {
    "is_reconstruction": True,
    "recomputed_from": "the bulk citations table and the census sampling frame",
    "probe_artifact_recorded": False,
    "probe_output_recorded_in": (
        "prose, in the commit message of CitationRecord/tooling 17e063c"
    ),
    "corroborated": True,
    "note": (
        "The census scope probe wrote no artifact. Its output directory was "
        "never created and nothing was committed, so its figures cannot be "
        "read back from any file. The first run's per-volume counts do "
        "survive, in the commit message that added the probe: F. Supp. 3d as "
        "90, 99, 0, 0 and Cal. App. 5th as 74, 0, 0, 0. This reconstruction "
        "reproduces both exactly, so it is corroborated rather than merely "
        "plausible. A commit message carries no schema, no provenance stamp, "
        "no population and no caveats, which is how a 3-of-4 sample rate came "
        "to travel as a 75% coverage share."
    ),
    "measures_differ": (
        "The probe counted opinions per volume through the CourtListener API "
        "against the live index. This counts citation rows per volume in a "
        "named bulk generation. The two are related and not identical, so an "
        "exact agreement is evidence and not a guarantee."
    ),
}


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


def _reporter(key: str):
    """The frame's own entry for a reporter, or None."""
    for reporter in PROBE_FRAME:
        if reporter.key == key:
            return reporter
    return None


def _volumes_document(args, reporter, result, source: dict) -> dict:
    """Three measurements, kept apart, plus what the corpus actually reaches."""
    drawn = sample_volumes(reporter, args.per_reporter)
    drawn_zeros = result.zeros_among(drawn)
    declared_zeros = result.zeros_across(reporter.first_volume, reporter.last_volume)
    declared_span = reporter.last_volume - reporter.first_volume + 1
    beyond = result.beyond_last_held(drawn)
    last_held = result.last_volume_held

    sampled_rate = len(drawn_zeros) / len(drawn) if drawn else None
    declared_rate = len(declared_zeros) / declared_span if declared_span else None

    return {
        "schema": VOLUMES_SCHEMA,
        "artifact_kind": RECONSTRUCTION_KIND,
        "generation": args.generation,
        "source": source,
        "reconstruction": RECONSTRUCTION,
        "reporter": {
            "key": reporter.key,
            "name": reporter.name,
            "category": reporter.category,
            "declared_first_volume": reporter.first_volume,
            "declared_last_volume": reporter.last_volume,
            "range_source": reporter.range_source,
        },
        # 1. The draw. A property of the sample, not of the corpus.
        "sampled_draw": {
            "per_reporter": args.per_reporter,
            "volumes": drawn,
            "strata": [{"name": s.name, "low": s.low, "high": s.high}
                       for s in strata(reporter)],
            "rows_by_volume": {str(v): result.rows_in(v) for v in drawn},
            "clusters_by_volume": {str(v): result.clusters_in(v) for v in drawn},
            "zero_volumes": drawn_zeros,
            "zero_count": len(drawn_zeros),
            "zero_rate": sampled_rate,
        },
        # 2. The declared range. A property of the frame's parameter.
        "declared_range": {
            "first_volume": reporter.first_volume,
            "last_volume": reporter.last_volume,
            "volumes": declared_span,
            "zero_count": len(declared_zeros),
            "zero_rate": declared_rate,
            "first_zero_volume": declared_zeros[0] if declared_zeros else None,
        },
        # 3. The extent. The only one of the three that is about the corpus.
        "extent": {
            "first_volume_held": result.first_volume_held,
            "last_volume_held": last_held,
            "volumes_held": len(result.volumes_held),
            "sampled_beyond_last_held": beyond,
            "sampled_beyond_last_held_count": len(beyond),
        },
        "measurements": [
            {"name": "sampled_zero_rate",
             "value": sampled_rate,
             "population": f"the {len(drawn)} volumes the stratified frame draws",
             "is_not": "a corpus coverage share"},
            {"name": "declared_range_zero_rate",
             "value": declared_rate,
             "population": (f"volumes {reporter.first_volume}-"
                            f"{reporter.last_volume} of the declared range"),
             "is_not": "a corpus coverage share"},
            {"name": "last_volume_held",
             "value": last_held,
             "population": "every volume carrying a citation in this reporter",
             "is_not": "a rate"},
        ],
        "histogram": {str(v): result.rows_in(v) for v in result.volumes_held},
        "citation_rows": {
            "matched": result.rows_matched,
            "scanned": result.rows_scanned,
            "without_cluster": result.rows_without_cluster,
            "non_numeric_volumes": result.non_numeric_volumes,
        },
        "caveats": VOLUME_CAVEATS,
        "measured_at_utc": iso_utc(),
        "provenance": _provenance(),
    }


def _report_volumes(document: dict) -> None:
    reporter = document["reporter"]
    draw = document["sampled_draw"]
    declared = document["declared_range"]
    extent = document["extent"]
    key = reporter["key"]

    _out("")
    _out(f"{key}, generation {document['generation']}")
    _out("  reconstruction from bulk data; the probe recorded nothing")
    _out("")
    _out(f"  1. the draw: {len(draw['volumes'])} volumes from the stratified frame")
    for volume in draw["volumes"]:
        rows = draw["rows_by_volume"][str(volume)]
        flag = "   <- zero" if rows == 0 else ""
        _out(f"       volume {volume:>4}   {rows:>6,} citation rows{flag}")
    _out(f"       {draw['zero_count']} of {len(draw['volumes'])} zero"
         f"   = {draw['zero_rate']:.0%}")
    _out("")
    _out(f"  2. the declared range {declared['first_volume']}-{declared['last_volume']}")
    _out(f"       {declared['zero_count']} of {declared['volumes']} volumes zero"
         f"   = {declared['zero_rate']:.1%}")
    _out("")
    _out("  3. what the corpus reaches")
    _out(f"       volumes held        {extent['first_volume_held']}"
         f"-{extent['last_volume_held']}  ({extent['volumes_held']} carrying citations)")
    _out(f"       sampled past the end {extent['sampled_beyond_last_held_count']}"
         f" of {len(draw['volumes'])}  {extent['sampled_beyond_last_held']}")
    _out("")
    _out("  These are three measurements over three populations. Only the third")
    _out("  says where the corpus stops.")


def cmd_volumes(args) -> int:
    reporter = _reporter(args.reporter)
    if reporter is None:
        keys = ", ".join(f'"{r.key}"' for r in PROBE_FRAME)
        _note(f"'{args.reporter}' is not in the census sampling frame.")
        _note(f"The frame declares: {keys}")
        _note("The draw is reconstructed from the frame, so the reporter has "
              "to be one it describes.")
        return 1

    directory = Path(args.dir) if args.dir else default_directory(args.generation)
    source = _source(directory, args.generation)
    path = directory / source["file"]
    if not path.is_file():
        _note(f"no citations file at {path}")
        _note("fetch the generation first: python -m bulk fetch --only citations")
        return 2

    _note(f"reading   {path}")
    _note(f"reporter  {reporter.key}")

    def progress(rows: int) -> None:
        _note(f"  scanned {rows:,} rows")

    result = count_volumes(path, reporter.key,
                           on_progress=None if args.quiet else progress)

    if result.rows_matched == 0:
        _note("")
        _note(f"'{reporter.key}' matched no citation row. Reporter names are "
              "matched exactly as stored; check the spelling before believing "
              "a zero.")
        return 1

    document = _volumes_document(args, reporter, result, source)
    _report_volumes(document)

    destination = Path(args.out) if args.out else DEFAULT_RESULTS_DIR / (
        f"volumes-{_slug(reporter.key)}-{args.generation}.json")
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

    volumes = subparsers.add_parser(
        "volumes",
        help="count one reporter volume by volume, and reconstruct the "
             "census draw over it")
    volumes.add_argument("--reporter", required=True, metavar="NAME",
                         help="a reporter the census frame declares")
    volumes.add_argument("--per-reporter", type=int, default=4, metavar="N",
                         help="volumes the frame draws (default: 4, as probed)")
    volumes.add_argument("--generation", default=DEFAULT_GENERATION,
                         metavar="YYYY-MM-DD")
    volumes.add_argument("--dir", metavar="PATH",
                         help="bulk data directory (default: the generation's)")
    volumes.add_argument("--out", metavar="FILE",
                         help=f"artifact path (default: under {DEFAULT_RESULTS_DIR})")
    volumes.add_argument("--json", action="store_true", help="also emit the artifact")
    volumes.add_argument("--quiet", action="store_true", help="no progress on stderr")
    volumes.set_defaults(func=cmd_volumes)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
