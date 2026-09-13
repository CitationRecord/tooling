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
import re
import sys
from pathlib import Path

from bulk import DEFAULT_GENERATION
from bulk.config import default_directory

from . import CATEGORIES, METADATA_KINDS, __version__
from . import draw as draw_mod
from . import exclude, pool as pool_mod, queryset, reviewed, truth
from .config import (
    DEFAULT_WORKDIR,
    DOCKET_NAME_PATTERNS,
    MALFORMED_NAME_PATTERNS,
    MAX_CASE_NAME_LENGTH,
    MAX_FILED_YEAR,
    UnsafeLocation,
    workdir,
)
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


_DOCKET_NAME = [re.compile(p) for p in DOCKET_NAME_PATTERNS]
_MALFORMED_NAME = [re.compile(p) for p in MALFORMED_NAME_PATTERNS]


def _metadata_check(lists):
    """Draw-time checks, so every rejection is recorded with its rule.

    These could have been pool filters. They are not, because a candidate
    filtered out during the pool build leaves no trace, and a draw that cannot
    say what it passed over is only half a record.
    """
    def check(candidate):
        name = candidate.get("case_name") or ""

        year = candidate.get("year") or ""
        if not year.isdigit() or int(year) > MAX_FILED_YEAR:
            return f"filed after {MAX_FILED_YEAR}, too recent to be uncited"

        # Docket shapes are checked before length. Such a name is usually also
        # over-long, and the specific reason is worth more in the record than
        # the symptom it causes.
        for pattern in _DOCKET_NAME:
            if pattern.search(name):
                return "docket entry rather than a case name"
        for pattern in _MALFORMED_NAME:
            if pattern.search(name):
                return "punctuation damage in the case name"
        if len(name) > MAX_CASE_NAME_LENGTH:
            return "case name longer than a case name"

        # A citation naming several cases under-specifies the query built on
        # it, whatever the case name says. Two drawn items failed this way at
        # resolve, where six cases matched one citation and three matched
        # another. Checked here so the rejection is on the record.
        if candidate.get("citation_shared"):
            return "citation matches more than one case"

        return exclude.reason_any(
            lists, name=name,
            citation=f"{candidate['volume']} {candidate['reporter']} "
                     f"{candidate['page']}",
            cluster_id=candidate.get("cluster_id"))
    return check


def _parenthetical_check(lists):
    def check(candidate):
        return reject_reason(candidate.get("text", ""))
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
    reviewed_path = reviewed.path_for(work, args.edition)
    decisions = reviewed.load(reviewed_path)
    sections = []

    meta = pools["metadata"]
    taken_clusters = set()

    reviewed_metadata = reviewed.check(decisions, "metadata")

    def metadata_unique(candidate):
        if candidate["cluster_id"] in taken_clusters:
            return "cluster already used in this edition"
        judged = reviewed_metadata(candidate["cluster_id"])
        if judged:
            return judged
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
    reviewed_para = reviewed.check(decisions, "negated-parenthetical")

    def parenthetical_check(candidate):
        judged = reviewed_para(candidate["parenthetical_id"])
        if judged:
            return judged
        return _parenthetical_check(lists)(candidate)

    result = draw_mod.select(
        para["candidates"], seed, "negated-parenthetical",
        PARENTHETICAL_COUNT, identify=lambda c: c["parenthetical_id"],
        check=parenthetical_check)
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
        reviewer_rejections=reviewed.entries(reviewed_path),
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
            _out(f"  Q  (awaiting resolve) {query.get('text_template')}")
        if query.get("subject", {}).get("case_name"):
            s = query["subject"]
            _out(f"  re {s['case_name']}, {s['citation']} ({s['year']}), "
                 f"cited {s.get('citation_count')} time(s)")
        if query.get("negation"):
            _out(f"  -  {query['negation']['original']}")
            _out(f"  +  {query['negation']['negated']}")
            _out(f"  rule {query['negation']['rule']}")
        _out(f"  truth {query.get('ground_truth') or 'NOT RECORDED'}")
        _out("")
    return 0


def cmd_reject(args) -> int:
    """Record a reviewer's rejection so the draw can consult it as data."""
    try:
        work = workdir(args.workdir)
    except UnsafeLocation as refusal:
        _note(str(refusal))
        return 3
    path = reviewed.path_for(work, args.edition)
    try:
        entry = reviewed.add(path, args.candidate, args.category,
                             args.reason, args.by)
    except ValueError as clash:
        _note(str(clash))
        return 1
    _out(f"recorded  {entry['candidate_id']}  {entry['category']}")
    _out(f"  reason  {entry['reason']}")
    _out(f"  by      {entry['decided_by']} at {entry['decided_at_utc']}")
    _out(f"  file    {path}")
    _out("")
    _out("  Redraw on the same seed. The candidate will appear in the")
    _out("  rejection record marked as a reviewer decision, not a rule.")
    return 0


def _print_plan(plan) -> None:
    _out(f"{len(plan.steps)} item(s) to resolve")
    _out(f"  journal records to be written   {plan.journal_records}")
    _out(f"  API requests, upper bound       {plan.request_count}")
    _out("")
    for step in plan.steps:
        _out(f"[{step['query_id']}]  {step['kind']}")
        for request in step["requests"]:
            _out(f"    -> {request}")
        _out(f"    writes: {step['writes']}")
        _out("")


def _resolve_metadata(query, resolver, client) -> bool:
    subject = query["subject"]
    resolution = resolver.resolve(subject["citation"])
    if resolution.status != "resolved" or not resolution.cluster_id:
        query["provenance"] = {"lookup": resolution.status,
                               "reason": resolution.reason,
                               "citation": subject["citation"]}
        return False

    base = {
        "cluster_id": resolution.cluster_id,
        "case_name": resolution.case_name,
        "court": resolution.court,
        "court_id": resolution.court_id,
        "year": resolution.year,
        "citation": subject["citation"],
    }
    provenance = {
        "established_by": "resolve/ citation lookup, journalled",
        "retrieved_at_utc": resolution.retrieved_at_utc,
        "courtlistener_url": resolution.courtlistener_url,
        "api": "courtlistener/v4",
    }

    kind = query["metadata_kind"]
    if kind == "year":
        query["ground_truth"] = {"answer": resolution.year, **base}
    elif kind == "citation":
        if not resolution.court:
            query["provenance"] = {**provenance, "note": "no court returned"}
            return False
        query["text"] = query["text_template"].format(
            case_name=subject["case_name"], court=resolution.court,
            year=resolution.year)
        query["ground_truth"] = {"answer": subject["citation"], **base}
    else:
        found = truth.majority_author(client, resolution.cluster_id)
        provenance["author_lookup"] = {
            "endpoint": f"opinions?cluster={resolution.cluster_id}",
            "journalled": False,
            "source": found["source"],
        }
        if not found["author"]:
            query["provenance"] = provenance
            return False
        query["ground_truth"] = {"answer": found["author"],
                                 "opinion_id": found["opinion_id"],
                                 "opinion_type": found["opinion_type"], **base}
    query["provenance"] = provenance
    return True


def _resolve_parenthetical(query, resolver, client) -> bool:
    subject = query["subject"]
    opinion = client._request(
        "GET", f"opinions/{subject['described_opinion_id']}").json()
    cluster_ref = str(opinion.get("cluster") or "")
    cluster_id = cluster_ref.rstrip("/").split("/")[-1]
    if not cluster_id.isdigit():
        query["provenance"] = {"note": "opinion carries no cluster"}
        return False

    cluster = client.cluster(int(cluster_id))
    citations = cluster.get("citations") or []
    if not citations:
        query["provenance"] = {"note": "cluster carries no citation",
                               "cluster_id": cluster_id}
        return False
    first = citations[0]
    citation = (f"{first.get('volume')} {first.get('reporter')} "
                f"{first.get('page')}")

    resolution = resolver.resolve(citation)
    if resolution.status != "resolved" or not resolution.court:
        query["provenance"] = {"lookup": resolution.status,
                               "reason": resolution.reason,
                               "citation": citation}
        return False

    query["text"] = query["text_template"].format(
        jurisdiction=resolution.court, negated=query["negation"]["negated"])
    query["ground_truth"] = {
        "holding": query["negation"]["original"],
        "held_by": resolution.case_name,
        "citation": citation,
        "cluster_id": resolution.cluster_id,
        "court": resolution.court,
        "court_id": resolution.court_id,
        "year": resolution.year,
        "note": ("The query asks for a case holding the opposite of this. "
                 "Acceptable responses are recorded on the artifact."),
    }
    query["provenance"] = {
        "established_by": "opinions fetch, then resolve/ citation lookup",
        "opinion_endpoint": f"opinions/{subject['described_opinion_id']}",
        "retrieved_at_utc": resolution.retrieved_at_utc,
        "courtlistener_url": resolution.courtlistener_url,
        "api": "courtlistener/v4",
    }
    return True


def cmd_resolve(args) -> int:
    document = queryset.read(args.draft)
    plan = truth.plan_for(document)

    if args.dry_run:
        _print_plan(plan)
        _out("  Nothing was requested. Re-run without --dry-run to resolve.")
        return 0

    from resolve.cache import Cache
    from resolve.client import CourtListener, MissingToken, load_env
    from resolve.config import REPO_ROOT as RESOLVE_ROOT
    from resolve.config import Config
    from resolve.journal import Journal, run_provenance
    from resolve.resolver import Resolver

    load_env(RESOLVE_ROOT / ".env")
    config = Config().resolved()
    try:
        client = CourtListener(config)
    except MissingToken as exc:
        _note(f"error: {exc}")
        return 1

    config.out_dir.mkdir(parents=True, exist_ok=True)
    cache = Cache(config.cache_path, config.negative_max_age_days)
    journal = Journal(config.journal_path, run_provenance())
    resolver = Resolver(client, config, cache=cache, journal=journal)

    _note(f"journal   {config.journal_path}")
    resolved = failed = 0
    try:
        for query in document["queries"]:
            if query["category"] == "metadata":
                done = _resolve_metadata(query, resolver, client)
            else:
                done = _resolve_parenthetical(query, resolver, client)
            resolved += 1 if done else 0
            failed += 0 if done else 1
            _note(f"  {query['id']:<26} "
                  f"{'recorded' if done else 'INCOMPLETE'}")
    finally:
        client.close()
        cache.close()

    outstanding = [q["id"] for q in document["queries"]
                   if not q.get("ground_truth")]
    document["status"] = "complete" if not outstanding else "awaiting ground truth"
    document["incomplete"] = outstanding
    queryset.write(document, args.draft)

    _out("")
    _out(f"resolved {resolved} of {len(document['queries'])}")
    if failed:
        _out(f"  {failed} left incomplete rather than answered from a guess")
    _out(f"  status  {document['status']}")
    _out(f"  draft   {args.draft}")
    _out("")
    _out("  Commit and push the lookup journal. A verification that exists")
    _out("  only on this machine is not yet a record of anything.")
    return 0 if not failed else 1


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

    rejecter = subparsers.add_parser(
        "reject", help="record a reviewer rejection the draw will honour")
    rejecter.add_argument("--edition", required=True, metavar="ID")
    rejecter.add_argument("--candidate", required=True, metavar="ID")
    rejecter.add_argument("--category", required=True, choices=CATEGORIES)
    rejecter.add_argument("--reason", required=True, metavar="TEXT")
    rejecter.add_argument("--by", required=True, metavar="NAME",
                          help="who made the call")
    rejecter.add_argument("--workdir", metavar="PATH")
    rejecter.set_defaults(func=cmd_reject)

    resolver = subparsers.add_parser("resolve", help="record ground truth")
    resolver.add_argument("--draft", required=True, metavar="FILE")
    resolver.add_argument("--dry-run", action="store_true",
                          help="print what would be requested and written")
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
