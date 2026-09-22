"""Command line: read a review packet the way a reviewer would.

    py -m review show --packet PACKET.json
    py -m review show --packet PACKET.json --tier interpretation-dependent

The warning is printed first and again on every item, read from the artifact
rather than restated here, so the two cannot drift apart.
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from . import TIERS, __version__
from .packet import RESOLUTION_KINDS, read, resolved, unsourced_packet, write


def _utf8(stream):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    return stream


_STDOUT = _utf8(sys.stdout)


def _out(message: str = "") -> None:
    print(message, file=_STDOUT, flush=True)


def _wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(text or "", width=78,
                         initial_indent=indent, subsequent_indent=indent)


def cmd_show(args) -> int:
    document = read(args.packet)

    _out("=" * 78)
    _out(_wrap(document["warning"], ""))
    _out("=" * 78)
    _out("")
    _out(f"{document['edition']}  {document['status']}")
    _out(f"drafted by {document['drafted_by']}")
    counts = document["counts"]
    _out(f"{counts['items']} item(s): " +
         ", ".join(f"{n} {tier}" for tier, n in counts["by_tier"].items()))
    _out("")
    _out(_wrap(document["reviewer_guidance"]))
    _out("")

    for entry in document.get("exclusions", []):
        _out(f"  exclusions: {entry['source']}, covering "
             f"{entry['items_covered']} of {entry['items_total']} items and "
             f"{len(entry.get('topics', []))} topics")
    _out(f"  unsourced list: {document.get('unsourced_list')}")
    _out("")

    for entry in document["items"]:
        if args.tier and entry["tier"] != args.tier:
            continue
        _out("-" * 78)
        _out(f"[{entry['id']}]  {entry['category']}  ·  {entry['tier']}")
        _out(f"  {entry['status']}")
        _out("")
        _out("  QUESTION")
        _out(_wrap(entry["question"], "    "))
        _out("")
        drafted = entry["drafted_answer"]
        _out(f"  DRAFTED ANSWER  ({drafted['confidence']}, "
             f"by {drafted['drafted_by']})")
        _out(_wrap(drafted["text"], "    "))
        for hedge in drafted.get("hedges", []):
            _out(_wrap(f"unsure: {hedge}", "    ! "))
        _out("")
        auth = entry["authority"]
        _out(f"  AUTHORITY  {auth['name']}  ({auth['kind']}, pinned by "
             f"{auth['provenance_path']}/)")
        _out(_wrap(f"“{auth['quote']}”", "    "))
        for key in ("cite", "url", "sha256", "retrieved_at_utc",
                    "rule_amended", "cluster_id", "court", "year"):
            if auth.get(key):
                _out(f"      {key}: {auth[key]}")
        _out("")
        source = entry.get("question_source") or {}
        _out(f"  QUESTION FROM  {source.get('where', 'unstated')}")
        if source.get("url"):
            _out(f"                 {source['url']}")
        _out("")
        _out("  VERDICT   ____ correct   ____ incorrect   ____ too loose")
        _out("  NOTES     ______________________________________________")
        _out("  REVIEWER  name ____________  date ________  credit? ____")
        _out("")
    return 0


def cmd_resolve(args) -> int:
    """Move one entry off the unsourced list, recording what settled it.

    The entry is not deleted. It moves to `resolved` carrying its own original
    account of why it could not be grounded, so the record still shows how
    long the gap was open and what was tried.
    """
    document = read(args.list)
    matches = [e for e in document.get("entries", [])
               if args.match.lower() in (e.get("looked_for", "")
                                         + " " + e.get("question", "")).lower()]
    if len(matches) != 1:
        _out(f"--match selected {len(matches)} entries; it must select exactly one.")
        for e in document.get("entries", []):
            _out(f"  {e.get('looked_for')}")
        return 1

    entry = matches[0]
    authority = {"url": args.url, "sha256": args.sha256,
                 "retrieved_at_utc": args.retrieved,
                 "effective": args.effective, "name": args.name}
    try:
        record = resolved(entry, args.kind, authority, args.note)
    except ValueError as refusal:
        _out(str(refusal))
        return 1

    remaining = [e for e in document["entries"] if e is not entry]
    rebuilt = unsourced_packet(document["edition"], remaining,
                               list(document.get("resolved", [])) + [record])
    write(rebuilt, args.list)

    _out(f"resolved  {entry.get('looked_for')}")
    _out(f"  kind      {record['resolution_kind']}")
    _out(f"  authority {authority['url']}")
    _out(f"  sha256    {authority['sha256']}")
    _out(f"  retrieved {authority['retrieved_at_utc']}")
    _out(f"  note      {record['note']}")
    _out("")
    _out(f"  unsourced {rebuilt['counts']['unsourced']}  "
         f"resolved {rebuilt['counts']['resolved']}  "
         f"retired {rebuilt['counts']['retired']}")
    _out(f"  written   {args.list}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review",
        description="Read an attorney review packet.")
    parser.add_argument("--version", action="version",
                        version=f"review {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    show = subparsers.add_parser("show", help="print the packet for reading")
    show.add_argument("--packet", required=True, metavar="FILE")
    show.add_argument("--tier", choices=TIERS,
                      help="show only one tier")
    show.set_defaults(func=cmd_show)

    res = subparsers.add_parser(
        "resolve", help="move an entry off the unsourced list")
    res.add_argument("--list", required=True, metavar="FILE",
                     help="the unsourced list to amend")
    res.add_argument("--match", required=True, metavar="TEXT",
                     help="substring selecting exactly one entry")
    res.add_argument("--kind", required=True, choices=RESOLUTION_KINDS)
    res.add_argument("--name", required=True, metavar="TEXT",
                     help="the authority that settled it")
    res.add_argument("--url", required=True, metavar="URL")
    res.add_argument("--sha256", required=True, metavar="HEX")
    res.add_argument("--retrieved", required=True, metavar="YYYY-MM-DD")
    res.add_argument("--effective", metavar="TEXT",
                     help="the date the authority states for itself")
    res.add_argument("--note", required=True, metavar="TEXT",
                     help="what this means for the edition")
    res.set_defaults(func=cmd_resolve)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
