"""The measuring run.

Reuses the client, throttle and cache from resolve/. Measures one volume per
step, writes state after every one, and can be stopped and resumed at any
point. A rate limit ends the run cleanly rather than failing it: what was
measured stays measured.
"""

from __future__ import annotations

import time

from resolve.cache import Cache
from resolve.client import CourtListener, RateLimited, ResolverError, redact

from .frame import Plan, strata
from .store import ProbeState, Store, VolumeResult, iso_utc


class Stopped(Exception):
    """The run stopped early but everything measured is saved."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _stratum_for(reporter, volume: int) -> str:
    for band in strata(reporter):
        if band.low <= volume <= band.high:
            return band.name
    return "unknown"


class Probe:
    """Measures the volumes in a plan, resumably."""

    def __init__(self, client: CourtListener, store: Store, cache: Cache | None = None) -> None:
        self._client = client
        self._store = store
        self._cache = cache

    def state_for(self, plan: Plan, provenance: dict) -> ProbeState:
        """Load an interrupted run, or start one. The plan must not drift."""
        existing = self._store.load()
        planned = {r.key: list(v) for r, v in
                   ((r, plan.volumes[r.key]) for r in plan.reporters)}

        if existing is not None:
            if existing.plan == planned:
                return existing
            raise Stopped(
                "the saved probe measured a different set of volumes; "
                "finish or delete it before running a different plan"
            )

        return ProbeState(
            started_at_utc=iso_utc(),
            per_reporter=plan.per_reporter,
            plan=planned,
            frame=[r.as_dict() for r in plan.reporters],
            provenance=provenance,
        )

    def run(self, plan: Plan, state: ProbeState, budget: int | None = None,
            on_event=None) -> ProbeState:
        """Measure what is left. Returns as soon as the budget or quota ends."""
        emit = on_event or (lambda *a, **k: None)
        by_key = {r.key: r for r in plan.reporters}
        pending = state.remaining()
        spent = 0

        emit("plan", pending=len(pending), state=state)

        for reporter_key, volume in pending:
            if budget is not None and spent >= budget:
                emit("budget", spent=spent)
                break

            reporter = by_key[reporter_key]
            emit("volume_start", reporter=reporter, volume=volume,
                 done=len(state.measured), total=plan.total_volumes)
            started = time.monotonic()

            try:
                spent += self._presence(reporter_key, state)
                used_before = spent
                holdings = self._client.volume_holdings(reporter_key, volume)
                spent += 2 if not holdings["pages_are_complete"] else 1
            except RateLimited as exc:
                self._store.save(state)
                raise Stopped(
                    f"stopped on the CourtListener rate limit with "
                    f"{len(plan.volumes) and plan.total_volumes - len(state.measured)} "
                    "volume(s) left; rerun to continue",
                    retry_after=getattr(exc, "retry_after", None),
                ) from None
            except ResolverError as exc:
                result = VolumeResult(
                    reporter=reporter_key, volume=volume,
                    stratum=_stratum_for(reporter, volume),
                    opinion_count=0, page_low=None, page_high=None,
                    pages_sampled=0, pages_are_complete=False,
                    measured_at_utc=iso_utc(), requests_used=1,
                    error=redact(str(exc)),
                )
                state.record(result)
                self._store.save(state)
                emit("volume_error", reporter=reporter, volume=volume, result=result)
                continue

            pages = holdings["pages"]
            result = VolumeResult(
                reporter=reporter_key,
                volume=volume,
                stratum=_stratum_for(reporter, volume),
                opinion_count=holdings["count"],
                page_low=pages[0] if pages else None,
                page_high=pages[-1] if pages else None,
                pages_sampled=len(pages),
                pages_are_complete=holdings["pages_are_complete"],
                measured_at_utc=iso_utc(),
                requests_used=spent - used_before,
            )
            state.record(result)
            self._store.save(state)
            emit("volume_done", reporter=reporter, volume=volume, result=result,
                 seconds=time.monotonic() - started,
                 done=len(state.measured), total=plan.total_volumes)

        self._store.save(state)
        return state

    def _presence(self, reporter_key: str, state: ProbeState) -> int:
        """Check once per reporter whether CourtListener holds it at all."""
        if reporter_key in state.reporter_present:
            return 0
        seen = self._client.reporter_has_opinions(reporter_key)
        state.reporter_present[reporter_key] = seen
        return 1
