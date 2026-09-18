"""Command line: look at the protocol, look at the plan, then run.

    py -m runner protocols list
    py -m runner protocols verify
    py -m runner plan   --queryset SET.json --protocol v1 --systems all
    py -m runner probe  --systems all
    py -m runner run    --queryset SET.json --protocol v1 --systems all --pilot
    py -m runner verify --run RUN_ID

`plan` and `protocols` touch no network. `probe` sends one throwaway sentence
per system to check credentials and request shape. `run` is the only command
that spends a query, and a query spent is a query burned: an item that has been
put to a model can never appear in a published edition.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import protocol as protocol_mod
from . import run as run_mod
from . import systems as systems_mod
from . import transcript as transcript_mod
from .config import (
    Config,
    DEFAULT_JOURNAL,
    PROTOCOL_DIR,
    UnsafeLocation,
    credential,
    load_env,
    runs_dir,
    shadowed_credentials,
)


def _utf8(stream):
    """Query text is legal prose and a Windows console will mangle it.

    Reconfigured in place rather than wrapped: a fresh wrapper around stdout's
    buffer closes that buffer when collected, which truncates every later line.
    """
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    return stream


#: Streams already reconfigured, so each is done once and only once.
_CONFIGURED = set()


def _stdout():
    """The current stdout, reconfigured once.

    Resolved per call rather than captured at import. Binding the stream at
    import time makes the reconfigure happen exactly once, which is tidy, and
    also makes every line invisible to anything that replaces stdout later --
    a test harness, a pipe, a caller redirecting output. A component whose
    output cannot be captured cannot be tested on what it says.
    """
    stream = sys.stdout
    if id(stream) not in _CONFIGURED:
        _utf8(stream)
        _CONFIGURED.add(id(stream))
    return stream


def _out(message: str = "") -> None:
    print(message, file=_stdout(), flush=True)


def _note(message: str = "") -> None:
    print(message, file=sys.stderr, flush=True)


def _journal(args) -> Path:
    return Path(getattr(args, "journal", None) or DEFAULT_JOURNAL).expanduser().resolve()


def _read_queryset(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# -- protocols ------------------------------------------------------------


def cmd_protocols(args) -> int:
    journal = _journal(args)
    versions = protocol_mod.available(args.dir)
    if not versions:
        _out(f"no protocols in {Path(args.dir or PROTOCOL_DIR)}")
        return 0

    if args.action == "show":
        target = args.protocol or versions[-1]
        protocol = protocol_mod.load(target, args.dir)
        _out(json.dumps(protocol.body, ensure_ascii=False, indent=2,
                        sort_keys=True))
        return 0

    _out(f"{len(versions)} protocol(s) in {Path(args.dir or PROTOCOL_DIR)}")
    _out("")
    broken = 0
    for version in versions:
        try:
            protocol = protocol_mod.load(version, args.dir)
        except protocol_mod.ProtocolError as refusal:
            broken += 1
            _out(f"  {version:<6} BROKEN")
            _out(f"         {refusal}")
            continue
        record = protocol_mod.registration(protocol, journal)
        state = (f"registered {record['record_id']} on "
                 f"{record['registered_at_utc']}") if record else "NOT REGISTERED"
        _out(f"  {version:<6} {state}")
        _out(f"         canonical  {protocol.digest.sha256_canonical}")
        _out(f"         bytes      {protocol.digest.sha256_bytes}")
        _out(f"         form       {protocol.digest.canonical_form}")
        if not record:
            _out("         runner will refuse to call a model against this.")
            _out("         py -m register add --artifact "
                 f"{protocol.path} \\")
            _out("              --kind prompt-protocol --edition <ID>")
        _out("")
    return 1 if broken else 0


# -- plan -----------------------------------------------------------------


def _prepare(args, journal):
    """Everything `run` needs, with every refusal applied. Makes no call."""
    protocol = protocol_mod.load(args.protocol, getattr(args, "dir", None))
    protocol_record = protocol_mod.registration(protocol, journal)

    path = Path(args.queryset).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    document = _read_queryset(path)
    queryset_record = run_mod.is_registered(path, journal)

    run_mod.gate(document, registered=queryset_record is not None,
                 pilot_flag=bool(args.pilot))

    chosen = systems_mod.select(args.systems)
    plan = run_mod.build_plan(document, path, protocol, chosen,
                              bool(args.pilot))
    plan.protocol_record = protocol_record
    plan.queryset_record = queryset_record
    return plan


def _print_header(plan) -> None:
    document = plan.document
    _out(f"{document.get('edition', '?')}   "
         f"{len(document.get('queries', []))} queries x "
         f"{len(plan.systems)} systems = {plan.conversations} conversations")
    _out("")
    if plan.pilot:
        _out("  PILOT. Nothing this produces is Edition One.")
        reason = document.get("registrable_reason")
        if reason:
            for line in _wrap(reason, 68):
                _out(f"    {line}")
        _out("")
    _out(f"  protocol   {plan.protocol.version}  "
         f"{plan.protocol.digest.sha256_canonical[:16]}...")
    _out(f"  registered {plan.protocol_record['record_id']}"
         if plan.protocol_record else "  registered NO")
    _out(f"  query set  {plan.queryset_path.name}")
    _out(f"  registered {plan.queryset_record['record_id']}"
         if plan.queryset_record else "  registered NO (a pilot set is not)")
    _out("")
    _out("  systems")
    for system in plan.systems:
        scope = "published" if system.published_scope else "OUT OF SCOPE"
        block = plan.protocol.block(system.id)
        _out(f"    {system.id:<8} {system.label:<8} {block['model']:<20} {scope}")
    outside = [s for s in plan.systems if not s.published_scope]
    if outside:
        _out("")
        _out("  Out of published scope: "
             + ", ".join(s.label for s in outside))
        for line in _wrap(systems_mod.OUT_OF_SCOPE, 68):
            _out(f"    {line}")
    _out("")


def _wrap(text: str, width: int) -> list:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def cmd_plan(args) -> int:
    journal = _journal(args)
    try:
        plan = _prepare(args, journal)
    except run_mod.GateRefused as refusal:
        _note(str(refusal))
        return 4
    except protocol_mod.ProtocolError as refusal:
        _note(str(refusal))
        return 2
    except FileNotFoundError as missing:
        _note(f"no such file: {missing}")
        return 2

    _print_header(plan)

    if not plan.protocol_record:
        _out("  The protocol is not registered. `run` will refuse with exit 5.")
        _out("")

    missing = run_mod.missing_credentials(plan.systems)
    if missing:
        _out("  credentials missing")
        for system in missing:
            _out(f"    {system.env_var:<22} {system.label}")
        _out("")

    for step in plan.steps:
        text = step.query.get("text") or "(no text: the set is incomplete)"
        _out(f"  [{step.sequence:>3}] {step.query['id']}  ->  {step.system.id}")
        if args.verbose:
            for line in _wrap(text, 66):
                _out(f"        {line}")
    _out("")
    _out(f"  {plan.conversations} conversations. Nothing was sent.")
    return 0


# -- probe ----------------------------------------------------------------


def _warn_shadowed() -> None:
    """Say which credentials the environment is overriding .env with.

    Not an error: exported variables beating .env is the documented
    precedence. But a failure has to be diagnosed against the credential that
    was actually sent, and this is the only place that difference is visible.
    """
    shadowed = shadowed_credentials()
    if not shadowed:
        return
    _note("note: the environment overrides .env for "
          + ", ".join(shadowed) + ".")
    _note("      An exported variable wins over the file. If you just edited "
          ".env, the value you edited is not the one being sent.")


def cmd_probe(args) -> int:
    """One throwaway sentence per system, to check credentials and shape.

    Sent so that a wrong request shape is found here rather than on the first
    real query. Nothing is recorded as a transcript: this is not a result and
    must not be able to look like one.
    """
    load_env()
    _warn_shadowed()
    config = Config(timeout=args.timeout).resolved()
    protocol = protocol_mod.load(args.protocol, getattr(args, "dir", None))
    chosen = systems_mod.select(args.systems)
    session = run_mod.session_for()

    _out("probe: one throwaway request per system. Nothing is recorded.")
    _out("")
    failures = 0
    try:
        for system in chosen:
            key = credential(system.env_var)
            if not key:
                _out(f"  {system.id:<8} NO CREDENTIAL  ({system.env_var})")
                failures += 1
                continue
            block = dict(protocol.block(system.id))
            block["max_output_tokens"] = 64
            step = run_mod.Step(
                query={"id": "probe", "text": "Reply with the word OK.",
                       "category": None},
                system=system, sequence=0)
            probe_protocol = protocol
            try:
                method, url, headers, body = system.adapter.build(
                    block, step.query["text"], probe_protocol.system_prompt, key)
                payload = json.dumps(body, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")
                from .adapters import wire as wire_mod
                wire = wire_mod.send(session, method, url,
                                     {**headers, "user-agent": "CitationRecord-runner/0.1"},
                                     payload, timeout=config.timeout,
                                     max_retries=1)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                _out(f"  {system.id:<8} ERROR  {exc.__class__.__name__}: {exc}")
                failures += 1
                continue

            if wire.transport_error:
                _out(f"  {system.id:<8} TRANSPORT  {wire.transport_error}")
                failures += 1
                continue
            parsed = None
            try:
                parsed = json.loads(wire.response_body)
            except json.JSONDecodeError:
                pass
            read = system.adapter.extract(parsed) if parsed is not None else {}
            if wire.ok and read.get("text") is not None:
                _out(f"  {system.id:<8} ok     HTTP {wire.http_status}  "
                     f"reported {read.get('reported_model')}")
            else:
                failures += 1
                _out(f"  {system.id:<8} FAILED HTTP {wire.http_status}")
                body = (wire.response_body or "")[:400]
                for line in _wrap(body, 66):
                    _out(f"           {line}")
    finally:
        session.close()

    _out("")
    _out(f"  {len(chosen) - failures} of {len(chosen)} reachable")
    return 1 if failures else 0


# -- run ------------------------------------------------------------------


def cmd_run(args) -> int:
    load_env()
    _warn_shadowed()
    journal = _journal(args)
    config = Config(runs=args.runs, journal=journal,
                    timeout=args.timeout).resolved()

    try:
        plan = _prepare(args, journal)
    except run_mod.GateRefused as refusal:
        _note(str(refusal))
        return 4
    except protocol_mod.ProtocolError as refusal:
        _note(str(refusal))
        return 2
    except FileNotFoundError as missing:
        _note(f"no such file: {missing}")
        return 2

    if not plan.protocol_record and not args.unregistered_protocol:
        _note(f"refusing to call a model against protocol "
              f"{plan.protocol.version}: it is not in the registration "
              f"journal at {journal}.")
        _note("  A result is not a result without the prompt protocol version "
              "attached, and a version attached to nothing is a string.")
        _note(f"  py -m register add --artifact {plan.protocol.path} "
              f"--kind prompt-protocol --edition <ID>")
        return 5

    missing = run_mod.missing_credentials(plan.systems)
    if missing:
        _note("missing credentials, so these systems cannot be reached:")
        for system in missing:
            _note(f"  {system.env_var:<22} {system.label}")
        _note("Put them in tooling/.env or export them. Nothing was sent.")
        return 1

    incomplete = [q["id"] for q in plan.document.get("queries", [])
                  if not q.get("text")]
    if incomplete and not args.allow_incomplete:
        _note(f"refusing: {len(incomplete)} queries have no text yet "
              f"({', '.join(incomplete[:3])}...).")
        _note("Run `py -m sample resolve` first. A query with no text asks "
              "the model nothing and burns the item anyway.")
        return 2

    # Preflight. Items are burned by being asked, not by being answered, so a
    # run that spends the whole set to collect three rejections has destroyed
    # the set for nothing. One throwaway sentence per system settles it.
    if not args.allow_unreachable:
        preflight = run_mod.session_for()
        unreachable = []
        try:
            for system in plan.systems:
                ok, detail = run_mod.reachable(preflight, system,
                                               plan.protocol, config)
                _note(f"  preflight  {system.id:<8} "
                      f"{'ok  ' + str(detail) if ok else 'UNREACHABLE  ' + str(detail)}")
                if not ok:
                    unreachable.append((system, detail))
        finally:
            preflight.close()
        if unreachable:
            _note("")
            _note(f"refusing to run: {len(unreachable)} of {len(plan.systems)} "
                  f"systems will not answer.")
            for system, detail in unreachable:
                _note(f"  {system.id:<8} {detail}")
            _note("")
            _note("  Nothing was sent and no item was spent. Every query in "
                  "this set is burned by being asked, whether or not a system "
                  "answers, so a run with systems missing costs the whole set "
                  "and returns part of the data.")
            _note("  Fix the systems, or name the ones that work with "
                  "--systems, or pass --allow-unreachable to spend the set "
                  "anyway.")
            return 1

    try:
        root = runs_dir(config.runs)
    except UnsafeLocation as refusal:
        _note(str(refusal))
        return 3

    label = plan.document.get("edition") or "run"
    run_id = transcript_mod.new_run_id(label, plan.pilot)
    store = transcript_mod.RunStore(root, run_id, seal=not args.no_seal)

    _print_header(plan)
    _out(f"  run        {run_id}")
    _out(f"  writing    {store.root}")
    _out("")

    store.snapshot(transcript_mod.PROTOCOL_SNAPSHOT, plan.protocol.body)
    store.snapshot(transcript_mod.QUERYSET_SNAPSHOT, plan.document)
    store.manifest(_manifest(plan, run_id, store))

    session = run_mod.session_for()
    counts = {"response": 0, "transport-failure": 0, "rejected-by-api": 0}
    try:
        for step in plan.steps:
            step.attempt = store.next_attempt(step.query["id"], step.system.id)
            transcript = run_mod.converse(session, step, plan.protocol, run_id,
                                          plan.pilot, config)
            line = store.record(transcript)
            counts[transcript["outcome"]] = counts.get(
                transcript["outcome"], 0) + 1
            mark = {"response": "ok",
                    "transport-failure": "TRANSPORT",
                    "rejected-by-api": "HTTP"}[transcript["outcome"]]
            status = transcript["response"]["http_status"]
            length = len(transcript["response"].get("text") or "")
            _note(f"  [{step.sequence:>3}] {step.query['id']:<22} "
                  f"{step.system.id:<8} {mark:<10} {status or '-':<5} "
                  f"{length:>6} chars")
    finally:
        session.close()

    _out("")
    _out(f"  responses           {counts.get('response', 0)}")
    _out(f"  rejected by API     {counts.get('rejected-by-api', 0)}")
    _out(f"  transport failures  {counts.get('transport-failure', 0)}")
    _out(f"  run        {store.root}")
    _out("")
    if plan.pilot:
        _out("  These items are now burned. They have been put to models and")
        _out("  must never appear in a published edition.")
        _out("")
    _out("  py -m runner verify --run " + run_id)
    return 1 if counts.get("transport-failure") else 0


def _manifest(plan, run_id: str, store) -> dict:
    document = plan.document
    return {
        "schema": transcript_mod.RUN_SCHEMA if hasattr(
            transcript_mod, "RUN_SCHEMA") else "citationrecord.runner.run.v1",
        "run_id": run_id,
        "pilot": plan.pilot,
        "registrable": False if plan.pilot else None,
        "registrable_reason": document.get("registrable_reason"),
        "started_at_utc": transcript_mod.iso_utc(),
        "edition": document.get("edition"),
        "queryset": {
            "filename": plan.queryset_path.name,
            "registration": plan.queryset_record,
            "status": document.get("status"),
        },
        "protocol": plan.protocol.as_dict(),
        "protocol_registration": plan.protocol_record,
        "systems": [s.as_dict() for s in plan.systems],
        "systems_outside_published_scope": [
            s.id for s in plan.systems if not s.published_scope],
        "queries": [{"id": q["id"], "category": q.get("category")}
                    for q in document.get("queries", [])],
        "order": "query-major; systems in registry order; sequence recorded "
                 "on every transcript",
        "provenance": transcript_mod.provenance(),
        "no_assessment": (
            "runner/ sends and records. No model classified, scored, ranked or "
            "flagged anything in this run, and no transcript carries a field "
            "that could hold a judgment."),
    }


# -- verify ---------------------------------------------------------------


def cmd_verify(args) -> int:
    root = Path(args.run)
    if not root.is_dir():
        root = Path(args.runs or runs_dir()) / args.run
    if not root.is_dir():
        _note(f"no such run: {args.run}")
        return 2

    report = transcript_mod.verify(root)
    cover = transcript_mod.coverage(root)

    _out(f"{report['run_id']}")
    _out(f"  index lines        {report['lines']}")
    _out(f"  chain              {'intact' if report['chain_ok'] else 'BROKEN'}")
    if report["chain_break"]:
        _out(f"    {report['chain_break']}")
    _out(f"  hash mismatches    {len(report['mismatched'])}")
    for name in report["mismatched"]:
        _out(f"    {name}")
    _out(f"  missing files      {len(report['missing'])}")
    for name in report["missing"]:
        _out(f"    {name}")
    _out(f"  unindexed files    {len(report['unindexed'])}")
    for name in report["unindexed"]:
        _out(f"    {name}")
    _out(f"  coverage           {cover['present']} of {cover['expected']}")
    for cell in cover["absent"]:
        _out(f"    absent  {cell}")

    failed = (not report["chain_ok"] or report["mismatched"]
              or report["missing"] or report["unindexed"])
    if failed:
        _out("")
        _out("  A break is evidence, not an inconvenience. Transcripts are")
        _out("  written once; a mismatch means one was edited or truncated.")
    return 1 if failed else 0


# -- parser ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runner",
        description="Execute a frozen prompt protocol and record the results.")
    parser.add_argument("--version", action="version",
                        version=f"runner {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def journal_arg(sub):
        sub.add_argument("--journal", metavar="FILE",
                         help=f"registration journal (default: {DEFAULT_JOURNAL})")

    def protocol_arg(sub):
        sub.add_argument("--protocol", default="v1", metavar="VERSION")
        sub.add_argument("--dir", metavar="PATH",
                         help="where protocol files live")

    protocols = subparsers.add_parser(
        "protocols", help="list, show or check protocol files")
    protocols.add_argument("action", nargs="?", default="list",
                           choices=("list", "show", "verify"))
    protocol_arg(protocols)
    journal_arg(protocols)
    protocols.set_defaults(func=cmd_protocols)

    planner = subparsers.add_parser(
        "plan", help="print every conversation that would happen; sends nothing")
    planner.add_argument("--queryset", required=True, metavar="FILE")
    planner.add_argument("--systems", nargs="*", default=["all"],
                         metavar="ID",
                         help="ids, or 'all', or 'published'")
    planner.add_argument("--pilot", action="store_true")
    planner.add_argument("--verbose", action="store_true",
                         help="print the query text of every step")
    protocol_arg(planner)
    journal_arg(planner)
    planner.set_defaults(func=cmd_plan)

    prober = subparsers.add_parser(
        "probe", help="one throwaway request per system; records nothing")
    prober.add_argument("--systems", nargs="*", default=["all"], metavar="ID")
    prober.add_argument("--timeout", type=float, default=120.0)
    protocol_arg(prober)
    prober.set_defaults(func=cmd_probe)

    runner = subparsers.add_parser("run", help="put the queries to the systems")
    runner.add_argument("--queryset", required=True, metavar="FILE")
    runner.add_argument("--systems", nargs="*", default=["all"], metavar="ID")
    runner.add_argument("--pilot", action="store_true",
                        help="the set declares pilot: true and registrable: false")
    runner.add_argument("--runs", metavar="PATH", help="where runs are written")
    runner.add_argument("--timeout", type=float, default=300.0)
    runner.add_argument("--no-seal", action="store_true",
                        help="do not set transcripts read-only after writing")
    runner.add_argument("--allow-incomplete", action="store_true",
                        help="run queries that have no text yet")
    runner.add_argument("--allow-unreachable", action="store_true",
                        help="spend the set even though a selected system "
                             "will not answer; the items are burned either way")
    runner.add_argument("--unregistered-protocol", action="store_true",
                        help="run against an unregistered protocol; every "
                             "transcript records that it was unregistered")
    runner.add_argument("--verbose", action="store_true")
    protocol_arg(runner)
    journal_arg(runner)
    runner.set_defaults(func=cmd_run)

    verifier = subparsers.add_parser(
        "verify", help="rehash a run and walk its chain")
    verifier.add_argument("--run", required=True, metavar="RUN_ID")
    verifier.add_argument("--runs", metavar="PATH")
    verifier.set_defaults(func=cmd_verify)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
