"""Command line: pool, then draw, then look, then resolve.

    py -m sample pool  --category metadata
    py -m sample pool  --category negated-parenthetical
    py -m sample draw  --edition 2026.Q4
    py -m sample show  --draft DRAFT.json
    py -m sample resolve --draft DRAFT.json

Three stages, deliberately separate. `pool` and `draw` touch no network.
`resolve` writes irreversible records to the lookup journal, so it runs only
after somebody has looked at the draw.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from bulk import DEFAULT_GENERATION
from bulk.config import default_directory

from . import CATEGORIES, METADATA_KINDS, __version__
from . import draw as draw_mod
from . import exclude, pool as pool_mod, queryset
from .config import DEFAULT_WORKDIR, UnsafeLocation, workdir
from .negate import negate, reject_reason

#: Category A: two authorship, one year, one citation.
METADATA_PLAN = (("author", 2), ("year", 1), ("citation", 1))
PARENTHETICAL_COUNT = 5


def _utf8(stream):
    """Console review is this component's one human-facing surface.

    Windows consoles default to a codepage that cannot render the curly
    quotes, dashes and section signs that legal text is full of, so a review
    command left to the default prints replacement characters over exactly the
    passages a reviewer is there to check. The data is fine; the display was
    not.

    Reconfigured in place rather than wrapped. A fresh TextIOWrapper around
    stdout's buffer closes that buffer when it is collected, which silently
    truncates every later line.
    """
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    return stream


_STDOUT = _utf8(sys.stdout)


def _out(message: str = "") -> None:
    print(message, file=_STDOUT, flush=True)


def _note(message: str = "") -> None:
    print(message, file=sys.stderr, flush=True)


def _pool_path(work: Path, category: str, generation: str) -> Path:
    return work / f"pool-{category}-{generation}.json"


def cmd_pool(args) -> int:
    try:
        work = workdir(args.workdir)
    except UnsafeLocation as refusal:
        _note(str(refusal))
        return 3
    directory = Path(args.dir) if args.dir else default_directory(args.generation)

    def progress(what, rows):
        _note(f"  {what}: {rows:,} rows")

    if args.category == "metadata":
        citations = directory / f"citations-{args.generation}.csv.bz2"
        clusters = directory / f"opinion-clusters-{args.generation}.csv.bz2"
        for path in (citations, clusters):
            if not path.is_file():
                _note(f"missing {path}")
                return 2
        _note("scanning citations, then clusters. The clusters file is large.")
        built = pool_mod.metadata_pool(citations, clusters, args.generation,
                                       on_progress=progress)
    else:
        path = directory / f"parentheticals-{args.generation}.csv.bz2"
        if not path.is_file():
            _note(f"missing {path}")
            return 2
        built = pool_mod.parenthetical_pool(path, args.generation,
                                            on_progress=progress)

    destination = _pool_path(work, args.category, args.generation)
    pool_mod.write(built, destination)
    _out(f"{args.category} pool")
    _out(f"  rows scanned  {built.scanned:,}")
    _out(f"  pool size     {len(built.candidates):,}")
    _out(f"  written to    {destination}")
    return 0


def _metadata_check(lists):
    def check(candidate):
        why = exclude.reason_any(
            lists, name=candidate.get("case_name"),
            citation=f"{candidate['volume']} {candidate['reporter']} "
                     f"{candidate['page']}",
            cluster_id=candidate.get("cluster_id"))
        return why
    return check


def _parenthetical_check(lists):
    def check(candidate):
        why = reject_reason(candidate.get("text", ""))
        if why:
            return why
        return exclude.reason_any(lists, name=None,
                                  citation=None,
                                  cluster_id=None)
    return check


def cmd_draw(args) -> int:
    try:
        work = workdir(args.workdir)
    except UnsafeLocation as refusal:
        _note(str(refusal))
        return 3

    pools = {}
    for category in CATEGORIES:
        path = _pool_path(work, category, args.generation)
        if not path.is_file():
            _note(f"no {category} pool at {path}")
            _note(f"build it first: py -m sample pool --category {category}")
            return 2
        pools[category] = pool_mod.read(path)

    seed = args.seed or draw_mod.new_seed()
    lists = exclude.load_all()
    sections, queries_by_section = [], []

    meta = pools["metadata"]
    taken_clusters = set()

    def metadata_unique(candidate):
        if candidate["cluster_id"] in taken_clusters:
            return "cluster already used in this edition"
        return _metadata_check(lists)(candidate)

    for kind, count in METADATA_PLAN:
        result = draw_mod.select(
            meta["candidates"], seed, f"metadata:{kind}", count,
            identify=lambda c: c["cluster_id"], check=metadata_unique)
        for candidate in result.taken:
            taken_clusters.add(candidate["cluster_id"])
        section = result.as_dict()
        section["metadata_kind"] = kind
        section["filters"] = meta["filters"]
        section["queries"] = [queryset.metadata_query(kind, c)
                              for c in result.taken]
        sections.append(section)

    para = pools["negated-parenthetical"]
    result = draw_mod.select(
        para["candidates"], seed, "negated-parenthetical",
        PARENTHETICAL_COUNT, identify=lambda c: c["parenthetical_id"],
        check=_parenthetical_check(lists))
    section = result.as_dict()
    section["filters"] = para["filters"]
    section["queries"] = []
    for candidate in result.taken:
        negation = negate(candidate["text"])
        section["queries"].append(
            queryset.parenthetical_query(candidate, negation))
    sections.append(section)

    document = queryset.build(
        edition=args.edition,
        generation=args.generation,
        seed=seed,
        sections=sections,
        exclusions=[entry.as_dict() for entry in lists],
        sources=[{"category": c, "pool_file": _pool_path(
            work, c, args.generation).name,
            "pool_size": len(pools[c]["candidates"]),
            "generation": args.generation} for c in CATEGORIES],
    )

    destination = Path(args.out) if args.out else (
        work / f"queryset-{args.edition}-draft.json")
    queryset.write(document, destination)

    _out(f"drew {len(document['queries'])} queries for {args.edition}")
    for section in sections:
        label = section.get("metadata_kind") or section["category"]
        _out(f"  {label:<22} took {section['taken']} of {section['wanted']}, "
             f"examined {section['examined']}, "
             f"rejected {len(section['rejected'])}")
    _out("")
    _out(f"  seed     {seed}")
    _out(f"  status   {document['status']}")
    _out(f"  written  {destination}")
    _out("")
    _out("  Look at the draw before resolving. resolve makes API calls that")
    _out("  write journal records that cannot be taken back.")
    return 0


def cmd_show(args) -> int:
    document = queryset.read(args.draft)
    _out(f"{document['edition']}  {document['status']}  "
         f"generation {document['generation']}")
    for entry in document.get("exclusions", []):
        _out(f"  exclusions: {entry['source']} covering "
             f"{entry['items_covered']} of {entry['items_total']} items")
    _out("")
    for query in document["queries"]:
        _out(f"[{query['id']}]  {query['category']}")
        if query.get("text"):
            _out(f"  Q  {query['text']}")
        else:
            _out(f"  Q  (awaiting jurisdiction) {query['text_template']}")
        if query.get("negation"):
            _out(f"  -  {query['negation']['original']}")
            _out(f"  +  {query['negation']['negated']}")
            _out(f"  rule {query['negation']['rule']}")
        _out(f"  truth {query.get('ground_truth') or 'NOT RECORDED'}")
        _out("")
    return 0


def cmd_resolve(args) -> int:
    _note("resolve is not implemented in this revision.")
    _note("The draw is offline and complete; ground truth needs the resolver "
          "wired in, which is the next commit and the first one that touches "
          "the network.")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sample",
        description="Draw queries with recorded ground truth.")
    parser.add_argument("--version", action="version",
                        version=f"sample {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def common(sub):
        sub.add_argument("--generation", default=DEFAULT_GENERATION,
                         metavar="YYYY-MM-DD")
        sub.add_argument("--workdir", metavar="PATH",
                         help=f"drafts and pools (default: {DEFAULT_WORKDIR})")

    pool = subparsers.add_parser("pool", help="build a candidate pool")
    pool.add_argument("--category", required=True, choices=CATEGORIES)
    pool.add_argument("--dir", metavar="PATH", help="bulk data directory")
    common(pool)
    pool.set_defaults(func=cmd_pool)

    drawer = subparsers.add_parser("draw", help="select queries, offline")
    drawer.add_argument("--edition", required=True, metavar="ID")
    drawer.add_argument("--seed", metavar="HEX",
                        help="re-draw an earlier selection; omit for a new one")
    drawer.add_argument("--out", metavar="FILE")
    common(drawer)
    drawer.set_defaults(func=cmd_draw)

    shower = subparsers.add_parser("show", help="print a draft for review")
    shower.add_argument("--draft", required=True, metavar="FILE")
    shower.set_defaults(func=cmd_show)

    resolver = subparsers.add_parser("resolve", help="record ground truth")
    resolver.add_argument("--draft", required=True, metavar="FILE")
    common(resolver)
    resolver.set_defaults(func=cmd_resolve)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
