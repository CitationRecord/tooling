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
from .packet import read


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
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
