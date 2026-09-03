"""Command line interface for the coverage scope probe.

    py -m coverage plan       show what would be measured, and the cost
    py -m coverage run        measure, resumably; rerun to continue
    py -m coverage report     write the JSON and markdown from saved state
    py -m coverage status     how far along a run is

Exit codes: 0 complete, 1 an error, 3 stopped early with work still to do.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from resolve.cache import Cache
from resolve.client import CourtListener, MissingToken, load_env, redact
from resolve.config import REPO_ROOT
from resolve.journal import run_provenance

from . import __version__
from .frame import PROBE_FRAME, build_plan
from .probe import Probe, Stopped
from .report import build_document, write
from .store import Store

DEFAULT_OUT_DIR = REPO_ROOT.parent / "citation-coverage"

#: The user-scope hourly limit. Pacing to it keeps a long run from spending
#: its budget on rejected requests.
REQUESTS_PER_HOUR = 50


def _out(message: str = "") -> None:
    print(message, flush=True)


def _paths(args):
    out = Path(args.out).expanduser().resolve()
    return out, out / "probe-state.json", out / "coverage-probe.json", out / "coverage-probe.md"


def _plan(args):
    return build_plan(PROBE_FRAME, per_reporter=args.per_reporter)


def _describe_plan(plan) -> None:
    best, worst = plan.estimated_requests()
    _out(f"reporters  {len(plan.reporters)}   volumes {plan.total_volumes}")
    _out(f"requests   {best} best case, {worst} worst case")
    hours_best = best / REQUESTS_PER_HOUR
    hours_worst = worst / REQUESTS_PER_HOUR
    _out(f"wall clock {hours_best:.1f}h to {hours_worst:.1f}h at {REQUESTS_PER_HOUR}/hour")
    _out()
    for reporter in plan.reporters:
        volumes = ", ".join(str(v) for v in plan.volumes[reporter.key])
        _out(f"  {reporter.key:<14} {reporter.category:<20} volumes {volumes}")
        _out(f"  {'':14} range 1-{reporter.last_volume} ({reporter.range_source})")


def cmd_plan(args) -> int:
    _out("SCOPE PROBE, not a census. These numbers cannot be published as section 4.2.")
    _out()
    _describe_plan(_plan(args))
    return 0


def _on_event(event: str, **kwargs) -> None:
    if event == "plan":
        _out(f"{kwargs['pending']} volume(s) still to measure")
        _out()
    elif event == "volume_start":
        _out(f"[{kwargs['done'] + 1}/{kwargs['total']}] "
             f"{kwargs['reporter'].key} volume {kwargs['volume']} ...")
    elif event == "volume_done":
        result = kwargs["result"]
        span = (
            f"pages {result.page_low}-{result.page_high}"
            if result.page_low is not None else "no pages observed"
        )
        truncated = "" if result.pages_are_complete else " (sample truncated)"
        _out(f"        {result.opinion_count} opinion(s), {span}{truncated}  "
             f"[{result.stratum}, {kwargs['seconds']:.1f}s]")
    elif event == "volume_error":
        _out(f"        FAILED {kwargs['result'].error}")
    elif event == "budget":
        _out(f"\nstopped: request budget of {kwargs['spent']} reached")


def cmd_run(args) -> int:
    out_dir, state_path, json_path, markdown_path = _paths(args)
    load_env(REPO_ROOT / ".env")

    try:
        client = CourtListener()
    except MissingToken as exc:
        _out(f"error: {redact(str(exc))}")
        return 1

    plan = _plan(args)
    store = Store(state_path)
    cache = Cache(out_dir / "coverage-cache.sqlite3")
    probe = Probe(client, store, cache)

    _out("SCOPE PROBE, not a census. Sample is too small to publish as section 4.2.")
    _out(f"state      {state_path}")
    _out(f"pacing     {REQUESTS_PER_HOUR}/hour shared user quota")
    _out()

    try:
        state = probe.state_for(plan, run_provenance(args.methodology))
    except Stopped as exc:
        _out(f"error: {exc}")
        return 1

    _describe_plan(plan)
    _out()

    stopped = None
    try:
        state = probe.run(plan, state, budget=args.budget, on_event=_on_event)
    except Stopped as exc:
        stopped = exc
    finally:
        client.close()
        cache.close()

    remaining = len(state.remaining())
    document = build_document(state)
    write(document, json_path, markdown_path)

    _out()
    _out(f"measured   {len(state.measured)}/{plan.total_volumes} volume(s)")
    _out(f"requests   {state.requests_used} used")
    _out(f"json       {json_path}")
    _out(f"markdown   {markdown_path}")

    if stopped is not None:
        _out()
        _out(f"stopped: {stopped}")
        if stopped.retry_after:
            _out(f"retry in about {stopped.retry_after:.0f}s, or rerun later")
        return 3
    if remaining:
        _out(f"\n{remaining} volume(s) left; rerun to continue")
        return 3
    _out("\ncomplete")
    return 0


def cmd_status(args) -> int:
    _, state_path, _, _ = _paths(args)
    state = Store(state_path).load()
    if state is None:
        _out(f"no probe state at {state_path}")
        return 0
    plan = _plan(args)
    done, total = len(state.measured), plan.total_volumes
    remaining = total - done
    _out(f"{state_path}")
    _out(f"measured {done}/{total} volume(s), {state.requests_used} request(s) used")
    _out(f"started {state.started_at_utc}, updated {state.updated_at_utc}")
    if remaining:
        hours = remaining * 1.5 / REQUESTS_PER_HOUR
        _out(f"{remaining} left, roughly {hours:.1f}h more at {REQUESTS_PER_HOUR}/hour")
        return 3
    _out("complete")
    return 0


def cmd_report(args) -> int:
    out_dir, state_path, json_path, markdown_path = _paths(args)
    state = Store(state_path).load()
    if state is None:
        _out(f"no probe state at {state_path}; run the probe first")
        return 1
    document = build_document(state)
    write(document, json_path, markdown_path)
    _out(f"json      {json_path}")
    _out(f"markdown  {markdown_path}")
    if args.show:
        _out()
        _out(markdown_path.read_text(encoding="utf-8"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coverage",
        description="Measure how densely CourtListener holds each reporter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"coverage {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def common(sub):
        sub.add_argument("--out", default=str(DEFAULT_OUT_DIR), metavar="DIR",
                         help=f"output directory (default: {DEFAULT_OUT_DIR})")
        sub.add_argument("--per-reporter", type=int, default=4, metavar="N",
                         help="volumes sampled per reporter (default: 4)")

    plan_parser = subparsers.add_parser("plan", help="show what would be measured")
    common(plan_parser)
    plan_parser.set_defaults(func=cmd_plan)

    run_parser = subparsers.add_parser("run", help="measure, resumably")
    common(run_parser)
    run_parser.add_argument("--budget", type=int, default=None, metavar="N",
                            help="stop after roughly N requests this session")
    run_parser.add_argument("--methodology", metavar="VERSION",
                            help="methodology version to record in provenance")
    run_parser.set_defaults(func=cmd_run)

    status_parser = subparsers.add_parser("status", help="how far along a run is")
    common(status_parser)
    status_parser.set_defaults(func=cmd_status)

    report_parser = subparsers.add_parser("report", help="write outputs from saved state")
    common(report_parser)
    report_parser.add_argument("--show", action="store_true", help="print the markdown")
    report_parser.set_defaults(func=cmd_report)

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
        _out("\ninterrupted; measured volumes are saved, rerun to continue")
        return 130


if __name__ == "__main__":
    sys.exit(main())
