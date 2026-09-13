"""Building candidate pools from the bulk drop.

Separated from the draw because it is the expensive half. The metadata pool
needs two passes, one over citations and one over the much larger clusters
file, and neither depends on the seed. Building once per generation and drawing
from the result many times keeps a re-draw cheap, and keeps the draw itself
offline.

A pool file records the filters that produced it. A draw that cannot say what
population it drew from is not reproducible, whatever its seed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bulk.read import read_dicts

from . import POOL_SCHEMA, __version__
from .config import (
    EXCLUDED_REPORTERS,
    MAX_CITATION_COUNT,
    MIN_PARENTHETICAL_SCORE,
    PREFERRED_REPORTERS,
    REQUIRED_PRECEDENTIAL_STATUS,
)
from .negate import reject_reason


def iso_utc() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


@dataclass
class Pool:
    """A candidate population, and the filters that defined it."""

    category: str
    generation: str
    filters: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    scanned: int = 0
    built_at_utc: str = ""

    def as_dict(self) -> dict:
        return {
            "schema": POOL_SCHEMA,
            "category": self.category,
            "generation": self.generation,
            "sample_version": __version__,
            "filters": self.filters,
            "rows_scanned": self.scanned,
            "pool_size": len(self.candidates),
            "built_at_utc": self.built_at_utc or iso_utc(),
            "candidates": self.candidates,
        }


def write(pool: Pool, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(pool.as_dict(), handle, ensure_ascii=False, indent=2,
                  sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def read(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# metadata


def metadata_pool(citations_path, clusters_path, generation: str,
                  on_progress=None) -> Pool:
    """Clusters obscure enough to test retrieval rather than recall.

    Pass one keeps one citation per eligible cluster. Pass two reads the
    clusters file for the case name, year, citation count and status, and
    applies the obscurity filter. Two passes rather than one because the
    clusters file is twenty times the size and there is no point carrying rows
    through it for clusters already excluded by reporter.
    """
    filters = {
        "excluded_reporters": sorted(EXCLUDED_REPORTERS),
        "preferred_reporters": sorted(PREFERRED_REPORTERS),
        "max_citation_count": MAX_CITATION_COUNT,
        "required_precedential_status": REQUIRED_PRECEDENTIAL_STATUS,
        "requires_year": True,
        "obscurity": (
            "citation_count <= %d, status %s, a preferred reporter, and never "
            "a Supreme Court reporter. Obscurity is these fields, not a "
            "judgment about the case."
            % (MAX_CITATION_COUNT, REQUIRED_PRECEDENTIAL_STATUS)
        ),
    }

    wanted: dict = {}
    first_cluster_at: dict = {}
    shared: set = set()
    scanned = 0
    for row in read_dicts(citations_path, ("cluster_id", "reporter", "volume",
                                           "page")):
        scanned += 1
        if on_progress and scanned % 2_000_000 == 0:
            on_progress("citations", scanned)
        reporter = row["reporter"]
        if reporter in EXCLUDED_REPORTERS or reporter not in PREFERRED_REPORTERS:
            continue
        cluster = row["cluster_id"]
        if not cluster:
            continue
        if not (row["volume"] or "").strip().isdigit():
            continue

        # Whether this citation names one case or several. The bulk table
        # carries a row per cluster and never says how many clusters sit at a
        # page, so a citation matching six cases looks identical here to one
        # matching a single case. Two drawn queries were built on ambiguous
        # citations before anything counted this.
        #
        # Tracked as first-seen plus a set of keys known to be shared, rather
        # than a set of clusters per key: the answer needed is only whether
        # more than one cluster is present, and holding a set for every one of
        # two million citations would cost hundreds of megabytes to learn it.
        key = (reporter, row["volume"], row["page"])
        seen = first_cluster_at.get(key)
        if seen is None:
            first_cluster_at[key] = cluster
        elif seen != cluster:
            shared.add(key)

        if cluster in wanted:
            continue
        wanted[cluster] = {
            "cluster_id": cluster,
            "reporter": reporter,
            "volume": row["volume"],
            "page": row["page"],
        }

    candidates = []
    cluster_rows = 0
    for row in read_dicts(clusters_path, ("id", "case_name", "date_filed",
                                          "citation_count",
                                          "precedential_status")):
        cluster_rows += 1
        if on_progress and cluster_rows % 2_000_000 == 0:
            on_progress("clusters", cluster_rows)
        entry = wanted.get(row["id"])
        if entry is None:
            continue
        if (row["precedential_status"] or "") != REQUIRED_PRECEDENTIAL_STATUS:
            continue
        try:
            if int(row["citation_count"] or 0) > MAX_CITATION_COUNT:
                continue
        except ValueError:
            continue
        name = (row["case_name"] or "").strip()
        filed = (row["date_filed"] or "").strip()
        if not name or len(filed) < 4 or not filed[:4].isdigit():
            continue
        key = (entry["reporter"], entry["volume"], entry["page"])
        candidates.append({**entry, "case_name": name, "date_filed": filed,
                           "year": filed[:4],
                           "citation_count": int(row["citation_count"] or 0),
                           "citation_shared": key in shared})

    filters["citation_uniqueness"] = (
        "Each candidate records whether its reporter, volume and page name "
        "more than one cluster. The draw rejects the shared ones, so the "
        "rejection is recorded rather than absorbed here."
    )
    ambiguous = sum(1 for c in candidates if c["citation_shared"])
    filters["candidates_with_shared_citations"] = ambiguous

    return Pool(category="metadata", generation=generation, filters=filters,
                candidates=candidates, scanned=scanned + cluster_rows,
                built_at_utc=iso_utc())


# --------------------------------------------------------------------------
# negated parentheticals


def parenthetical_pool(path, generation: str, on_progress=None) -> Pool:
    """Parentheticals that can be inverted by deleting a single negator."""
    filters = {
        "min_score": MIN_PARENTHETICAL_SCORE,
        "starts_with": "holding that",
        "negation": (
            "exactly one removable negator, no residual negation after "
            "removal, single clause, 60 to 220 characters. Rules are named in "
            "sample/negate.py and recorded on every item."
        ),
    }

    candidates = []
    scanned = 0
    for row in read_dicts(path, ("id", "text", "score", "described_opinion_id")):
        scanned += 1
        if on_progress and scanned % 2_000_000 == 0:
            on_progress("parentheticals", scanned)
        try:
            score = float(row["score"] or 0)
        except ValueError:
            continue
        if score < MIN_PARENTHETICAL_SCORE:
            continue
        if not row["described_opinion_id"]:
            continue
        text = (row["text"] or "").strip()
        if reject_reason(text) is not None:
            continue
        candidates.append({
            "parenthetical_id": row["id"],
            "described_opinion_id": row["described_opinion_id"],
            "score": score,
            "text": text,
        })

    return Pool(category="negated-parenthetical", generation=generation,
                filters=filters, candidates=candidates, scanned=scanned,
                built_at_utc=iso_utc())
