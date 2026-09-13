"""Command line: fix an artifact and record its hash before it is used.

    python -m register add --artifact QUERIES.json --kind query-set \
                           --edition 2026.Q4
    python -m register verify --artifact QUERIES.json
    python -m register list
    python -m register chain

`add` is the control. Run it before the first model call, not after, because a
hash recorded afterwards is consistent with an artifact assembled to suit the
results and no reader can tell the two apart.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import KINDS, __version__
from .config import DEFAULT_JOURNAL, PRIVATE_ROOT, UnsafeLocation, check_unpublished
from .digest import digest_file
from .journal import (
    append,
    build_record,
    find_by_digest,
    last_line_hash,
    read_records,
    verify_chain,
)


def _out(message: str = "") -> None:
    print(message, file=sys.stdout, flush=True)


def _note(message: str = "") -> None:
    print(message, file=sys.stderr, flush=True)


def _journal(args) -> Path:
    return Path(args.journal or DEFAULT_JOURNAL).expanduser().resolve()


def _describe(record: dict) -> str:
    artifact = record.get("artifact") or {}
    return (f"  {record['record_id']}  {record['kind']:<15} "
            f"{record.get('edition') or '-':<10} "
            f"{artifact.get('filename','?'):<28} "
            f"{record['registered_at_utc']}")


def refuses_registration(path):
    """Whether an artifact declares itself unregistrable, and why.

    A general contract rather than a special case: any artifact may carry
    registrable: false at its top level, and this refuses to hash it. A review
    packet pending sign-off, a partial query set, a draft of anything, each can
    decline in one line and nothing here needs to know what any of them are.

    Flipping the flag means editing the file, which shows in a diff. A set
    becomes registrable by a visible decision rather than by someone forgetting
    it was not.
    """
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict) or body.get("registrable") is not False:
        return None
    return (body.get("registrable_reason")
            or "the artifact carries registrable: false and gives no reason")


def cmd_add(args) -> int:
    journal = _journal(args)
    try:
        artifact = check_unpublished(args.artifact, private_root=args.private_root)
    except UnsafeLocation as refusal:
        _note(str(refusal))
        return 3
    if not artifact.is_file():
        _note(f"no such file: {artifact}")
        return 2

    refusal = refuses_registration(artifact)
    if refusal:
        _note(f"refusing to register {artifact.name}: {refusal}")
        _note("The artifact declares registrable: false. Registering it would "
              "fix content that is not meant to be fixed. To register it "
              "anyway, change that flag in the file, where the change is "
              "visible in a diff.")
        return 4

    digest = digest_file(artifact)
    existing = find_by_digest(journal, digest)
    if existing and not args.again:
        latest = existing[-1]
        _note(f"already registered as {latest['record_id']} on "
              f"{latest['registered_at_utc']}.")
        _note("Registering identical content twice records nothing new. Pass "
              "--again if a second record is genuinely wanted.")
        return 1

    if args.supersedes and not any(
            r["record_id"] == args.supersedes for r in read_records(journal)):
        _note(f"no record {args.supersedes} to supersede")
        return 2

    record = build_record(
        kind=args.kind,
        edition=args.edition,
        filename=artifact.name,
        digest=digest,
        prev_hash=last_line_hash(journal),
        note=args.note,
        supersedes=args.supersedes,
        label=args.label,
    )
    append(journal, record)

    _out(f"registered  {record['record_id']}")
    _out(f"  kind      {record['kind']}")
    _out(f"  edition   {record['edition']}")
    _out(f"  artifact  {artifact.name}  ({digest.bytes_len:,} bytes)")
    _out(f"  controls  {digest.controlling_kind}  {digest.controlling}")
    if digest.sha256_canonical:
        _out(f"  bytes     {digest.sha256_bytes}")
    _out(f"  at        {record['registered_at_utc']}")
    _out(f"  journal   {journal}")
    _out("")
    _out("  Commit and push the journal now. A registration that exists only")
    _out("  on this machine is not yet a record of anything.")
    return 0


def cmd_verify(args) -> int:
    journal = _journal(args)
    artifact = Path(args.artifact).expanduser().resolve()
    if not artifact.is_file():
        _note(f"no such file: {artifact}")
        return 2

    digest = digest_file(artifact)
    matches = find_by_digest(journal, digest)
    if not matches:
        _out(f"NOT REGISTERED  {artifact.name}")
        _out(f"  {digest.controlling_kind}  {digest.controlling}")
        _out("  No record in the journal matches this content. Either it was")
        _out("  never registered, or it has changed since it was.")
        return 1

    _out(f"REGISTERED  {artifact.name}")
    for record in matches:
        _out(_describe(record))
        if record.get("supersedes"):
            _out(f"    supersedes {record['supersedes']}")
    if digest.sha256_canonical:
        newest = matches[-1].get("artifact", {})
        if newest.get("sha256_bytes") != digest.sha256_bytes:
            _out("")
            _out("  Byte hash differs from the registered one; the canonical")
            _out("  hash matches. The data is identical and the file has been")
            _out("  reformatted or its line endings normalised.")
    return 0


def cmd_list(args) -> int:
    journal = _journal(args)
    records = read_records(journal)
    if args.kind:
        records = [r for r in records if r.get("kind") == args.kind]
    if args.edition:
        records = [r for r in records if r.get("edition") == args.edition]
    if not records:
        _out("no registrations")
        return 0
    _out(f"{len(records)} registration(s) in {journal}")
    for record in records:
        _out(_describe(record))
    return 0


def cmd_chain(args) -> int:
    journal = _journal(args)
    ok, index, detail = verify_chain(journal)
    if ok:
        _out(f"chain intact: {detail}")
        return 0
    _out(f"CHAIN BROKEN at record {index}")
    _out(f"  {detail}")
    _out("  A record has been edited or removed. The journal is append-only;")
    _out("  a break is evidence, not an inconvenience.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="register",
        description="Fix an artifact and record its hash before it is used.")
    parser.add_argument("--version", action="version",
                        version=f"register {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def common(sub):
        sub.add_argument("--journal", metavar="FILE",
                         help=f"registration journal (default: {DEFAULT_JOURNAL})")

    add = subparsers.add_parser("add", help="register an artifact")
    add.add_argument("--artifact", required=True, metavar="FILE")
    add.add_argument("--kind", required=True, choices=KINDS)
    add.add_argument("--edition", required=True, metavar="ID",
                     help='the edition this fixes, e.g. "2026.Q4"')
    add.add_argument("--label", metavar="TEXT", help="short human name")
    add.add_argument("--note", metavar="TEXT", help="why this was registered")
    add.add_argument("--supersedes", metavar="RECORD_ID",
                     help="the record this replaces; both are kept")
    add.add_argument("--again", action="store_true",
                     help="register identical content a second time")
    add.add_argument("--private-root", metavar="PATH",
                     help=f"the private artifact repository "
                          f"(default: {PRIVATE_ROOT})")
    common(add)
    add.set_defaults(func=cmd_add)

    verify = subparsers.add_parser(
        "verify", help="check an artifact against the journal")
    verify.add_argument("--artifact", required=True, metavar="FILE")
    common(verify)
    verify.set_defaults(func=cmd_verify)

    listing = subparsers.add_parser("list", help="show registrations")
    listing.add_argument("--kind", choices=KINDS)
    listing.add_argument("--edition", metavar="ID")
    common(listing)
    listing.set_defaults(func=cmd_list)

    chain = subparsers.add_parser("chain", help="check the journal chain")
    common(chain)
    chain.set_defaults(func=cmd_chain)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
