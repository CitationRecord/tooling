"""Counting clusters by the reporters their citations carry.

One streaming pass over the citations table. Only clusters carrying one of the
two reporters are held in memory, which for any real reporter pair is tens of
thousands of identifiers rather than the eighteen million rows scanned.

Reporter names are matched exactly as the bulk table stores them. No
normalisation, no fuzzy matching: a near-miss returns zero rather than a
plausible wrong number, and the caller is expected to treat zero as a
misspelling until it has checked otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bulk.read import read_dicts

#: Columns needed from the citations table.
COLUMNS = ("reporter", "cluster_id", "type")


@dataclass
class PairCount:
    """The result of one pass, in clusters and in rows."""

    reporter_a: str
    reporter_b: str
    clusters_a_only: int
    clusters_b_only: int
    clusters_both: int
    rows_a: int
    rows_b: int
    rows_scanned: int
    rows_without_cluster: int
    types_a: dict = field(default_factory=dict)
    types_b: dict = field(default_factory=dict)

    @property
    def clusters_a(self) -> int:
        """Clusters carrying reporter A, with or without B."""
        return self.clusters_a_only + self.clusters_both

    @property
    def clusters_b(self) -> int:
        return self.clusters_b_only + self.clusters_both

    @property
    def clusters_either(self) -> int:
        return self.clusters_a_only + self.clusters_b_only + self.clusters_both

    @property
    def a_only_share_of_a(self) -> float | None:
        """The share of A's clusters that carry no B citation.

        This is the number a per-reporter reading mistakes for a coverage gap.
        It is a statement about how citations are distributed between two
        reporters, not about whether any case is absent.
        """
        return (self.clusters_a_only / self.clusters_a) if self.clusters_a else None

    @property
    def b_only_share_of_b(self) -> float | None:
        return (self.clusters_b_only / self.clusters_b) if self.clusters_b else None


def count_pair(path, reporter_a: str, reporter_b: str,
               on_progress=None, every: int = 2_000_000) -> PairCount:
    """Count clusters carrying each reporter, and both, in a single pass."""
    if reporter_a == reporter_b:
        raise ValueError("the two reporters must differ")

    seen_a: set = set()
    seen_b: set = set()
    rows_a = rows_b = scanned = orphaned = 0
    types_a: dict = {}
    types_b: dict = {}

    for row in read_dicts(Path(path), COLUMNS):
        scanned += 1
        reporter = row["reporter"]
        if reporter == reporter_a:
            bucket, seen, types = "a", seen_a, types_a
        elif reporter == reporter_b:
            bucket, seen, types = "b", seen_b, types_b
        else:
            if on_progress and scanned % every == 0:
                on_progress(scanned)
            continue

        cluster = row["cluster_id"]
        if not cluster:
            # A citation with no cluster cannot be attributed to a case, so it
            # is counted and set aside rather than silently dropped.
            orphaned += 1
        else:
            seen.add(cluster)
            if bucket == "a":
                rows_a += 1
            else:
                rows_b += 1
            types[row["type"]] = types.get(row["type"], 0) + 1

        if on_progress and scanned % every == 0:
            on_progress(scanned)

    both = seen_a & seen_b
    return PairCount(
        reporter_a=reporter_a,
        reporter_b=reporter_b,
        clusters_a_only=len(seen_a) - len(both),
        clusters_b_only=len(seen_b) - len(both),
        clusters_both=len(both),
        rows_a=rows_a,
        rows_b=rows_b,
        rows_scanned=scanned,
        rows_without_cluster=orphaned,
        types_a=dict(sorted(types_a.items())),
        types_b=dict(sorted(types_b.items())),
    )
