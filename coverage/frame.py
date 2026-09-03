"""The sampling frame: which reporters, and which volumes of each.

The volume range for each reporter is a declared parameter, not something
read out of CourtListener. Asking CourtListener where its volumes stop and
then sampling only that range would hide exactly the gaps this measures: a
reporter running to volume 300 that CourtListener carries to volume 90 would
look complete. Ranges come from the reporter's own extent, and the source of
each is recorded in the output.

Sampling is stratified by era rather than uniform, because ingestion quality
varies by decade. Volumes are drawn from the early, middle, and recent thirds
of the range, with the extra draw going to the most recent third where
district and state coverage actually varies.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Reporter:
    """One reporter in the frame, with its declared volume range."""

    key: str
    name: str
    category: str
    first_volume: int
    last_volume: int
    range_source: str
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


#: The scope probe's four reporters. Deliberately small: this asks whether
#: district and state citations are usable as ground truth, against a
#: known-dense control.
PROBE_FRAME: tuple = (
    Reporter(
        key="U.S.",
        name="United States Reports",
        category="control",
        first_volume=1,
        last_volume=603,
        range_source="volume 603 is the most recent bound volume cited in this "
                     "session (Loper Bright, 603 U.S. 369, 2024)",
        note="Control. Expected dense; anything sparse here indicts the method, "
             "not the reporter.",
    ),
    Reporter(
        key="F.3d",
        name="Federal Reporter, Third Series",
        category="federal appellate",
        first_volume=1,
        last_volume=1000,
        range_source="F.3d ran from 1993 until it was superseded by F.4th in 2021, "
                     "ending near volume 1000",
    ),
    Reporter(
        key="F. Supp. 3d",
        name="Federal Supplement, Third Series",
        category="federal district",
        first_volume=1,
        last_volume=700,
        range_source="F. Supp. 3d began in 2014; volume 678 is attested in this "
                     "session, so the range is taken to about 700",
        note="Where the Mata v. Avianca lookup failed. The reason for this probe.",
    ),
    Reporter(
        key="Cal. App. 5th",
        name="California Appellate Reports, Fifth Series",
        category="state",
        first_volume=1,
        last_volume=100,
        range_source="Cal. App. 5th began in 2016 and is in the low hundreds",
    ),
)


@dataclass(frozen=True)
class Stratum:
    """One era-third of a reporter's range."""

    name: str
    low: int
    high: int


def strata(reporter: Reporter) -> tuple:
    """Split a reporter's volume range into early, middle and recent thirds."""
    first, last = reporter.first_volume, reporter.last_volume
    span = max(last - first + 1, 1)
    step = span / 3.0
    edges = [first + round(step * i) for i in range(4)]
    edges[-1] = last + 1
    names = ("early", "middle", "recent")
    return tuple(
        Stratum(names[i], edges[i], max(edges[i + 1] - 1, edges[i]))
        for i in range(3)
    )


def sample_volumes(reporter: Reporter, count: int = 4) -> list:
    """Pick volumes to measure, deterministically.

    Deterministic on purpose: a probe someone else re-runs must hit the same
    volumes, so there is no random seed to record and no way for a rerun to
    quietly land on friendlier books. Draws are the midpoint of each stratum,
    with any remainder going to the recent third first.
    """
    buckets = strata(reporter)
    allocation = [count // 3] * 3
    for i in range(count % 3):
        allocation[2 - i] += 1  # remainder to the recent end first

    picked: list = []
    for stratum, wanted in zip(buckets, allocation):
        if wanted <= 0:
            continue
        width = stratum.high - stratum.low + 1
        for slot in range(wanted):
            # Evenly spaced within the stratum, midpoint when only one.
            offset = int(round(width * (slot + 0.5) / wanted)) - 1
            volume = min(max(stratum.low + offset, stratum.low), stratum.high)
            while volume in picked and volume < stratum.high:
                volume += 1
            picked.append(volume)
    return sorted(dict.fromkeys(picked))


@dataclass
class Plan:
    """What a probe run intends to measure, before it measures anything."""

    reporters: tuple
    per_reporter: int
    volumes: dict = field(default_factory=dict)

    @property
    def total_volumes(self) -> int:
        return sum(len(v) for v in self.volumes.values())

    def estimated_requests(self) -> tuple:
        """Best and worst case request counts.

        One request per volume, plus a second when the volume holds more than
        one page of results, plus one presence check per reporter.
        """
        best = self.total_volumes + len(self.reporters)
        worst = self.total_volumes * 2 + len(self.reporters)
        return best, worst


def build_plan(reporters=PROBE_FRAME, per_reporter: int = 4) -> Plan:
    return Plan(
        reporters=tuple(reporters),
        per_reporter=per_reporter,
        volumes={r.key: sample_volumes(r, per_reporter) for r in reporters},
    )
