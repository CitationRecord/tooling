"""Excluding items used by prior published benchmarks.

Their construction methods are reusable. Their items are not: a set that has
been public since 2024 may have been seen by every system under test, and a
query drawn from it measures recall of the benchmark rather than of the law.

**This check is partial and says so.** The full item list for Magesh et al.
(2024) is not publicly obtainable: the OSF registration is not readable without
credentials, and the publisher blocks automated access to the article. What is
excluded here is what the paper's Appendix A prints verbatim, which is one
example query per category. That is 15 of their 202 items.

A partial check that reports its own coverage is worth more than an assertion
of non-overlap from a filter. It is worth less than the real thing, and the
coverage figure travels with every artifact so nobody later mistakes one for
the other.

The list is a data file rather than code. When the full set arrives, the file
grows and nothing here changes.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

LISTS_DIR = Path(__file__).resolve().parent / "exclusions"


def normalise(text: str) -> str:
    """Fold a case name to something two spellings of it share.

    Punctuation, case, accents and the v./vs. split all vary between sources,
    and an exclusion that misses because of a full stop is not an exclusion.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\bv(?:s|s\.|\.)?\s", " v ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


@dataclass
class ExclusionList:
    """Everything a draw must not select, and how much of a set it covers."""

    source: str = ""
    citation: str = ""
    coverage: str = ""
    items_covered: int = 0
    items_total: int = 0
    case_names: set = field(default_factory=set)
    citations: set = field(default_factory=set)
    cluster_ids: set = field(default_factory=set)
    topics: list = field(default_factory=list)

    @property
    def coverage_fraction(self) -> float | None:
        if not self.items_total:
            return None
        return self.items_covered / self.items_total

    def excludes_name(self, name: str) -> bool:
        folded = normalise(name)
        if not folded:
            return False
        return any(folded == known or known in folded or folded in known
                   for known in self.case_names)

    def excludes_citation(self, citation: str) -> bool:
        return normalise(citation) in self.citations

    def excludes_cluster(self, cluster_id) -> bool:
        return str(cluster_id) in self.cluster_ids

    def excludes_topic(self, question: str) -> str | None:
        """Which listed topic a question covers, if any.

        Case names and citations are the wrong key for some categories. A
        collision with a prior benchmark's local-rules or circuit-split item is
        the same court and the same rule, or the same circuit and the same
        statutory question, and no case name is involved at all. A topic
        matches when every one of its terms appears.
        """
        folded = normalise(question)
        if not folded:
            return None
        for topic in self.topics:
            terms = [normalise(t) for t in topic.get("all_of", []) if t]
            if terms and all(term in folded for term in terms):
                return topic.get("description") or topic.get("id")
        return None

    def reason(self, name=None, citation=None, cluster_id=None,
               question=None) -> str | None:
        """Why a candidate is excluded, or None if it is not."""
        if cluster_id is not None and self.excludes_cluster(cluster_id):
            return f"cluster in {self.source}"
        if citation and self.excludes_citation(citation):
            return f"citation in {self.source}"
        if name and self.excludes_name(name):
            return f"case name in {self.source}"
        if question:
            topic = self.excludes_topic(question)
            if topic:
                return f"topic in {self.source}: {topic}"
        return None

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "citation": self.citation,
            "coverage": self.coverage,
            "items_covered": self.items_covered,
            "items_total": self.items_total,
            "coverage_fraction": self.coverage_fraction,
            "case_names": sorted(self.case_names),
            "citations": sorted(self.citations),
            "cluster_ids": sorted(self.cluster_ids),
            "topics": list(self.topics),
        }


def load(path) -> ExclusionList:
    """Read one exclusion list file."""
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    return ExclusionList(
        source=body.get("source", ""),
        citation=body.get("citation", ""),
        coverage=body.get("coverage", ""),
        items_covered=int(body.get("items_covered") or 0),
        items_total=int(body.get("items_total") or 0),
        case_names={normalise(n) for n in body.get("case_names", []) if n},
        citations={normalise(c) for c in body.get("citations", []) if c},
        cluster_ids={str(c) for c in body.get("cluster_ids", [])},
        topics=list(body.get("topics", [])),
    )


def load_all(directory=None) -> list:
    """Every exclusion list shipped with this component."""
    directory = Path(directory or LISTS_DIR)
    if not directory.is_dir():
        return []
    return [load(p) for p in sorted(directory.glob("*.json"))]


def reason_any(lists, name=None, citation=None, cluster_id=None,
               question=None) -> str | None:
    for entry in lists:
        why = entry.reason(name=name, citation=citation,
                           cluster_id=cluster_id, question=question)
        if why:
            return why
    return None
