"""Citation parsing and reporter recognition.

Parsing uses eyecite, which is what CourtListener itself parses with, so a
citation this module accepts is one the API will recognise. Reporter
recognition uses reporters-db, the same reporter list eyecite is built on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from eyecite import get_citations
from eyecite.models import FullCaseCitation
from reporters_db import EDITIONS, REPORTERS, VARIATIONS_ONLY

# Fallback for input eyecite declines to parse. Deliberately loose: it only
# has to split the string, because the reporter is validated separately.
LOOSE_RE = re.compile(
    r"^\s*(?P<volume>\d+[a-zA-Z]?)\s+(?P<reporter>[A-Za-z0-9.'&\- ]+?)\s+(?P<page>\d+)",
)

# Citation forms that are not case law. CourtListener indexes court opinions,
# so a statute or a regulation is outside its coverage by definition, not
# missing from it.
NON_CASE_PATTERNS = (
    (re.compile(r"\bU\.?S\.?C\.?\b", re.I), "United States Code"),
    (re.compile(r"\bC\.?F\.?R\.?\b", re.I), "Code of Federal Regulations"),
    (re.compile(r"\bFed\.?\s*Reg\.?\b", re.I), "Federal Register"),
    (re.compile(r"\bStat\.\b"), "Statutes at Large"),
    (re.compile(r"\bPub\.?\s*L\.?\s*No", re.I), "public law"),
    (re.compile(r"\b(?:Const|Amend)\.", re.I), "constitutional provision"),
    (re.compile(r"\bL\.?\s*Rev\.?\b", re.I), "law review"),
    (re.compile(r"\bRestatement\b", re.I), "Restatement"),
)


@dataclass(frozen=True)
class ParsedCitation:
    """A citation split into its parts, with the reporter checked."""

    raw: str
    volume: str | None
    reporter: str | None
    page: str | None
    year: int | None = None
    court: str | None = None
    reporter_is_known: bool = False
    canonical_reporter: str | None = None
    parsed_by: str = "eyecite"

    @property
    def is_complete(self) -> bool:
        return bool(self.volume and self.reporter and self.page)

    @property
    def normalized(self) -> str | None:
        if not self.is_complete:
            return None
        return f"{self.volume} {self.canonical_reporter or self.reporter} {self.page}"


def non_case_citation(text: str) -> str | None:
    """Name the non-case authority this looks like, or None."""
    for pattern, label in NON_CASE_PATTERNS:
        if pattern.search(text):
            return label
    return None


def known_reporter(reporter: str) -> tuple[bool, str | None]:
    """Is this a reporter abbreviation reporters-db recognises?

    Returns (known, canonical form). Variations such as "U.S." vs "US" map
    back to the canonical edition string CourtListener indexes under.
    """
    if not reporter:
        return False, None

    candidate = reporter.strip().rstrip(",")
    if candidate in EDITIONS:
        return True, candidate
    if candidate in REPORTERS:
        return True, candidate
    if candidate in VARIATIONS_ONLY:
        canonical = VARIATIONS_ONLY[candidate]
        first = canonical[0] if isinstance(canonical, list) else canonical
        return True, first

    # Tolerate spacing differences, e.g. "So.3d" for "So. 3d".
    squashed = candidate.replace(" ", "")
    for table in (EDITIONS, VARIATIONS_ONLY):
        for key in table:
            if key.replace(" ", "") == squashed:
                value = table[key]
                if table is EDITIONS:
                    return True, key
                first = value[0] if isinstance(value, list) else value
                return True, first
    return False, None


def parse(text: str) -> ParsedCitation:
    """Split one citation string into volume, reporter, page, year, court.

    Pass a single citation. eyecite attaches a trailing year parenthetical to
    whichever citation precedes it, so a string holding several citations
    yields the wrong year for all but the last.
    """
    raw = (text or "").strip()

    for citation in get_citations(raw):
        if not isinstance(citation, FullCaseCitation):
            continue
        groups = citation.groups or {}
        reporter = groups.get("reporter")
        known, canonical = known_reporter(reporter or "")
        year = citation.year
        court = getattr(citation.metadata, "court", None)
        return ParsedCitation(
            raw=raw,
            volume=groups.get("volume"),
            reporter=reporter,
            page=groups.get("page"),
            year=int(year) if year else None,
            court=court,
            reporter_is_known=known,
            canonical_reporter=canonical,
            parsed_by="eyecite",
        )

    match = LOOSE_RE.match(raw)
    if match:
        reporter = match.group("reporter").strip()
        known, canonical = known_reporter(reporter)
        year_match = re.search(r"\((?:[^)]*?\b)?(1[6-9]\d{2}|20\d{2})\)", raw)
        return ParsedCitation(
            raw=raw,
            volume=match.group("volume"),
            reporter=reporter,
            page=match.group("page"),
            year=int(year_match.group(1)) if year_match else None,
            court=None,
            reporter_is_known=known,
            canonical_reporter=canonical,
            parsed_by="regex",
        )

    return ParsedCitation(
        raw=raw, volume=None, reporter=None, page=None, parsed_by="none"
    )
