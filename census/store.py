"""Resumable probe state.

Every measured volume is written to disk the moment it is measured, so a run
killed by a rate limit, a reboot, or three days of waiting resumes from the
next unmeasured volume rather than starting over. At 50 requests an hour a
restart from zero is not an inconvenience, it is a lost day.

The state file is JSON rather than a database: a probe that feeds a published
figure should be readable without tooling.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import ARTIFACT_KIND, SCHEMA


def iso_utc(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass
class VolumeResult:
    """One measured volume."""

    reporter: str
    volume: int
    stratum: str
    opinion_count: int
    page_low: int | None
    page_high: int | None
    pages_sampled: int
    pages_are_complete: bool
    measured_at_utc: str
    requests_used: int = 1
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def is_measured(self) -> bool:
        return self.error is None


@dataclass
class ProbeState:
    """Everything a run has learned so far."""

    started_at_utc: str = field(default_factory=iso_utc)
    updated_at_utc: str = field(default_factory=iso_utc)
    per_reporter: int = 4
    plan: dict = field(default_factory=dict)
    frame: list = field(default_factory=list)
    reporter_present: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    requests_used: int = 0
    provenance: dict = field(default_factory=dict)

    def key(self, reporter: str, volume: int) -> str:
        return f"{reporter}|{volume}"

    @property
    def measured(self) -> set:
        return {self.key(r["reporter"], r["volume"]) for r in self.results if not r.get("error")}

    def remaining(self) -> list:
        """Volumes still to measure, in plan order."""
        done = self.measured
        pending = []
        for reporter, volumes in self.plan.items():
            for volume in volumes:
                if self.key(reporter, volume) not in done:
                    pending.append((reporter, volume))
        return pending

    def record(self, result: VolumeResult) -> None:
        # A retry of a volume that previously errored replaces the error.
        target = self.key(result.reporter, result.volume)
        self.results = [
            r for r in self.results if self.key(r["reporter"], r["volume"]) != target
        ]
        self.results.append(result.as_dict())
        self.requests_used += result.requests_used
        self.updated_at_utc = iso_utc()


class Store:
    """Reads and writes the probe state file atomically."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> ProbeState | None:
        if not self.path.is_file():
            return None
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        payload = body.get("state") or {}
        known = ProbeState().__dict__.keys()
        return ProbeState(**{k: v for k, v in payload.items() if k in known})

    def save(self, state: ProbeState) -> None:
        """Write via a temp file and replace, so a kill cannot truncate it."""
        state.updated_at_utc = iso_utc()
        document = {
            "schema": SCHEMA,
            "artifact_kind": ARTIFACT_KIND,
            "warning": (
                "Scope probe, not a census. Sample size is too small to "
                "support a published coverage figure."
            ),
            "state": asdict(state),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
