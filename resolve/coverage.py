"""Does CourtListener actually hold this reporter and volume?

This is the module that separates "not found" from "not covered". A 404 from
the citation lookup means only that no case sits at that page. It does not
say whether CourtListener holds the surrounding volume at all, and without
that, a coverage gap is indistinguishable from a fabrication.

The test is empirical rather than assumed. It asks CourtListener how many
opinions it indexes for the cited volume, and if none, for the reporter as a
whole. Counts are cached per reporter and per volume, since they are shared
across every citation into the same book.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .client import CourtListener

#: CourtListener holds the volume, so a missing page is a real absence.
HELD = "volume_held"
#: CourtListener holds other volumes of this reporter, but not this one.
VOLUME_GAP = "volume_not_held"
#: CourtListener holds nothing at all in this reporter.
REPORTER_GAP = "reporter_not_held"
#: The probe could not run.
UNKNOWN = "unknown"


@dataclass(frozen=True)
class CoverageVerdict:
    verdict: str
    reporter: str
    volume: str | None
    reporter_opinion_count: int | None = None
    volume_opinion_count: int | None = None
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _quote(value: str) -> str:
    return value.replace('"', '\\"')


class CoverageProbe:
    """Reporter and volume holdings, cached for the life of the probe."""

    def __init__(self, client: CourtListener, cache=None) -> None:
        self._client = client
        self._cache = cache
        self._reporters: dict[str, int] = {}
        self._volumes: dict[tuple[str, str], int] = {}

    def _reporter_count(self, reporter: str) -> int:
        if reporter in self._reporters:
            return self._reporters[reporter]
        if self._cache is not None:
            stored = self._cache.get_coverage(reporter, None)
            if stored is not None:
                self._reporters[reporter] = stored
                return stored
        count = self._client.search_count(f'citation:("{_quote(reporter)}")')
        self._reporters[reporter] = count
        if self._cache is not None:
            self._cache.put_coverage(reporter, None, count)
        return count

    def _volume_count(self, reporter: str, volume: str) -> int:
        key = (reporter, volume)
        if key in self._volumes:
            return self._volumes[key]
        if self._cache is not None:
            stored = self._cache.get_coverage(reporter, volume)
            if stored is not None:
                self._volumes[key] = stored
                return stored
        count = self._client.search_count(f'citation:("{volume} {_quote(reporter)}")')
        self._volumes[key] = count
        if self._cache is not None:
            self._cache.put_coverage(reporter, volume, count)
        return count

    def check(self, reporter: str, volume: str | None) -> CoverageVerdict:
        """Classify CourtListener's holdings around a citation."""
        if not reporter:
            return CoverageVerdict(UNKNOWN, reporter or "", volume, detail="no reporter to probe")

        if volume:
            in_volume = self._volume_count(reporter, volume)
            if in_volume > 0:
                return CoverageVerdict(
                    HELD,
                    reporter,
                    volume,
                    volume_opinion_count=in_volume,
                    detail=(
                        f"CourtListener indexes {in_volume} opinion(s) in "
                        f"{volume} {reporter}, so the volume is held"
                    ),
                )
        else:
            in_volume = None

        in_reporter = self._reporter_count(reporter)
        if in_reporter == 0:
            return CoverageVerdict(
                REPORTER_GAP,
                reporter,
                volume,
                reporter_opinion_count=0,
                volume_opinion_count=in_volume,
                detail=f"CourtListener indexes no opinions in {reporter}",
            )

        return CoverageVerdict(
            VOLUME_GAP,
            reporter,
            volume,
            reporter_opinion_count=in_reporter,
            volume_opinion_count=in_volume,
            detail=(
                f"CourtListener indexes {in_reporter} opinion(s) in {reporter} "
                f"but none in volume {volume}"
            ),
        )
