"""Does CourtListener actually hold this reporter, volume, and page range?

This is the module that separates "not found" from "not covered". A 404 from
the citation lookup means only that no case sits at that page. It does not
say whether CourtListener holds the surrounding volume at all, and without
that, a coverage gap is indistinguishable from a fabrication.

A count alone is not enough either. CourtListener indexes exactly two
opinions in volume 678 F. Supp. 3d, both Court of International Trade cases
at pages 1369 and 1371. Treating that as "the volume is held" reported a real
S.D.N.Y. case at page 443 as missing. So when a volume is thinly held, the
probe asks which pages are actually there and whether the cited page falls
inside them.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .client import CourtListener

#: CourtListener holds this part of the volume, so a missing page is a real
#: absence.
HELD = "volume_held"
#: The volume has some opinions, but not around the cited page.
VOLUME_SPARSE = "volume_partially_held"
#: CourtListener holds other volumes of this reporter, but not this one.
VOLUME_GAP = "volume_not_held"
#: CourtListener holds nothing at all in this reporter.
REPORTER_GAP = "reporter_not_held"
#: The probe could not run.
UNKNOWN = "unknown"

#: At or above this many opinions, a volume is densely enough indexed that a
#: missing page is meaningful without checking which pages are present. A
#: bound reporter volume holds hundreds of cases; this is deliberately well
#: below that, and below it the page range is checked instead of assumed.
DENSE_VOLUME = 50

#: How far outside the observed page range still counts as inside the
#: ingested region, absorbing the fact that a sample may miss the true edges.
PAGE_MARGIN = 50


@dataclass(frozen=True)
class CoverageVerdict:
    verdict: str
    reporter: str
    volume: str | None
    page: str | None = None
    reporter_opinion_count: int | None = None
    volume_opinion_count: int | None = None
    held_page_low: int | None = None
    held_page_high: int | None = None
    sampled_pages: list = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _as_int(value) -> int | None:
    match = re.match(r"\d+", str(value or ""))
    return int(match.group()) if match else None


class CoverageProbe:
    """Reporter, volume, and page holdings, cached for the life of the probe."""

    def __init__(self, client: CourtListener, cache=None) -> None:
        self._client = client
        self._cache = cache
        self._reporters: dict[str, int] = {}
        self._volumes: dict[tuple[str, str], dict] = {}

    def _reporter_present(self, reporter: str) -> int:
        """Opinions visible for the reporter at all. Zero means not held."""
        if reporter in self._reporters:
            return self._reporters[reporter]
        if self._cache is not None:
            stored = self._cache.get_coverage(reporter, None)
            if stored is not None:
                self._reporters[reporter] = stored
                return stored
        seen = self._client.reporter_has_opinions(reporter)
        self._reporters[reporter] = seen
        if self._cache is not None:
            self._cache.put_coverage(reporter, None, seen)
        return seen

    def _volume(self, reporter: str, volume: str) -> dict:
        """Exact count and page list for one volume, cached per book."""
        key = (reporter, volume)
        if key in self._volumes:
            return self._volumes[key]
        holdings = self._client.volume_holdings(reporter, volume)
        self._volumes[key] = holdings
        if self._cache is not None:
            self._cache.put_coverage(reporter, volume, holdings["count"])
        return holdings

    def check(self, reporter: str, volume: str | None, page: str | None = None) -> CoverageVerdict:
        """Classify CourtListener's holdings around a citation."""
        if not reporter:
            return CoverageVerdict(UNKNOWN, reporter or "", volume, page,
                                   detail="no reporter to probe")

        in_volume = None
        if volume:
            holdings = self._volume(reporter, volume)
            in_volume = holdings["count"]
            if in_volume >= DENSE_VOLUME:
                return CoverageVerdict(
                    HELD, reporter, volume, page,
                    volume_opinion_count=in_volume,
                    detail=(
                        f"CourtListener indexes {in_volume} opinions in "
                        f"{volume} {reporter}, enough to treat the volume as held"
                    ),
                )
            if in_volume > 0:
                return self._sparse(reporter, volume, page, in_volume, holdings["pages"])

        present = self._reporter_present(reporter)
        if present == 0:
            return CoverageVerdict(
                REPORTER_GAP, reporter, volume, page,
                reporter_opinion_count=0, volume_opinion_count=in_volume,
                detail=f"CourtListener indexes no opinions in {reporter}",
            )

        return CoverageVerdict(
            VOLUME_GAP, reporter, volume, page,
            reporter_opinion_count=present, volume_opinion_count=in_volume,
            detail=(
                f"CourtListener indexes opinions in {reporter} "
                f"but none in volume {volume}"
            ),
        )

    def _sparse(self, reporter: str, volume: str, page: str | None,
                count: int, pages: list) -> CoverageVerdict:
        """A thinly held volume. Which pages are actually there?"""
        cited = _as_int(page)

        if not pages or cited is None:
            return CoverageVerdict(
                VOLUME_SPARSE, reporter, volume, page,
                volume_opinion_count=count, sampled_pages=pages,
                detail=(
                    f"CourtListener indexes only {count} opinion(s) in {volume} "
                    f"{reporter}, too few to treat the volume as held"
                ),
            )

        low, high = pages[0], pages[-1]
        if low - PAGE_MARGIN <= cited <= high + PAGE_MARGIN:
            return CoverageVerdict(
                HELD, reporter, volume, page,
                volume_opinion_count=count, held_page_low=low, held_page_high=high,
                sampled_pages=pages,
                detail=(
                    f"CourtListener holds pages {low}-{high} of {volume} "
                    f"{reporter}, which spans page {cited}"
                ),
            )

        return CoverageVerdict(
            VOLUME_SPARSE, reporter, volume, page,
            volume_opinion_count=count, held_page_low=low, held_page_high=high,
            sampled_pages=pages,
            detail=(
                f"CourtListener holds only {count} opinion(s) in {volume} "
                f"{reporter}, at pages {low}-{high}; page {cited} is outside "
                "the part of the volume it has"
            ),
        )
