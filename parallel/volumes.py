"""Per-volume counts for one reporter, across a bulk generation.

The companion to `pairs`. Where that asks how citations are distributed across
two reporters, this asks how they are distributed across one reporter's
volumes, which is what a per-volume sample rate is computed from.

Three quantities come out of the same pass and must not be conflated, because
conflating them is what put an unsupportable figure into circulation:

    the zero rate among a sample of volumes
    the zero rate across a reporter's declared range
    the highest volume the corpus actually holds

The first is a property of the draw. The second is a property of the declared
range. Only the third says anything about where the corpus stops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bulk.read import read_dicts

#: Columns needed from the citations table.
COLUMNS = ("reporter", "volume", "cluster_id")


@dataclass
class VolumeCount:
    """One reporter's citations, counted volume by volume."""

    reporter: str
    rows_by_volume: dict = field(default_factory=dict)
    clusters_by_volume: dict = field(default_factory=dict)
    rows_scanned: int = 0
    rows_matched: int = 0
    rows_without_cluster: int = 0
    non_numeric_volumes: dict = field(default_factory=dict)

    @property
    def volumes_held(self) -> list:
        """Numeric volumes carrying at least one citation, ascending."""
        return sorted(v for v, n in self.rows_by_volume.items() if n > 0)

    @property
    def first_volume_held(self) -> int | None:
        held = self.volumes_held
        return held[0] if held else None

    @property
    def last_volume_held(self) -> int | None:
        held = self.volumes_held
        return held[-1] if held else None

    def rows_in(self, volume: int) -> int:
        return self.rows_by_volume.get(volume, 0)

    def clusters_in(self, volume: int) -> int:
        return len(self.clusters_by_volume.get(volume, ()))

    def zeros_among(self, volumes) -> list:
        """Which of the given volumes carry no citation at all."""
        return [v for v in volumes if self.rows_in(v) == 0]

    def zeros_across(self, first: int, last: int) -> list:
        """Which volumes in an inclusive range carry no citation at all."""
        return [v for v in range(first, last + 1) if self.rows_in(v) == 0]

    def beyond_last_held(self, volumes) -> list:
        """Which of the given volumes sit past the corpus's highest volume.

        A zero here is not a gap in coverage. It is a volume the corpus does
        not reach, and the distinction is the whole point of this module.
        """
        last = self.last_volume_held
        if last is None:
            return list(volumes)
        return [v for v in volumes if v > last]


def count_volumes(path, reporter: str, on_progress=None,
                  every: int = 2_000_000) -> VolumeCount:
    """Count one reporter's citations per volume, in a single pass."""
    result = VolumeCount(reporter=reporter)

    for row in read_dicts(Path(path), COLUMNS):
        result.rows_scanned += 1
        if on_progress and result.rows_scanned % every == 0:
            on_progress(result.rows_scanned)
        if row["reporter"] != reporter:
            continue

        result.rows_matched += 1
        raw = (row["volume"] or "").strip()
        if not raw.isdigit():
            # Volume strings that are not plain integers are counted and set
            # aside rather than coerced, since a coerced volume would land in
            # the histogram as a number nobody can check.
            result.non_numeric_volumes[raw] = result.non_numeric_volumes.get(raw, 0) + 1
            continue

        volume = int(raw)
        result.rows_by_volume[volume] = result.rows_by_volume.get(volume, 0) + 1
        cluster = row["cluster_id"]
        if cluster:
            result.clusters_by_volume.setdefault(volume, set()).add(cluster)
        else:
            result.rows_without_cluster += 1

    return result
