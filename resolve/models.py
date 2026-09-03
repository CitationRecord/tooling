"""Outcome vocabulary and the resolution record.

Three outcomes are findings about a citation:

    resolved      CourtListener holds the cited case.
    not_found     CourtListener holds the cited volume but nothing at that
                  page. The citation is absent from a corpus that should
                  contain it.
    not_covered   CourtListener does not hold the reporter or the volume, or
                  the reporter is not a recognised US case reporter. Nothing
                  can be concluded about the citation from CourtListener.

Two further states are not findings about the citation and must never be
scored as one:

    ambiguous     More than one case matches. An attorney resolves it.
    error         The lookup failed: rate limit, network, bad token. This
                  says nothing at all about the citation.

`not_found` is the only outcome that supports a fabrication finding.
`not_covered` and `ambiguous` feed the unscorable bucket. A coverage gap
scored as a fabrication would inflate error rates against systems drawing on
corpora broader than CourtListener's.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

STATUS_RESOLVED = "resolved"
STATUS_NOT_FOUND = "not_found"
STATUS_NOT_COVERED = "not_covered"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_ERROR = "error"

#: Outcomes that may be counted toward the Class 1 mechanical score.
SCORABLE = frozenset({STATUS_RESOLVED, STATUS_NOT_FOUND})

#: Outcomes that go to the unscorable bucket for attorney adjudication.
UNSCORABLE = frozenset({STATUS_NOT_COVERED, STATUS_AMBIGUOUS})

#: Not a finding about the citation at all. Re-run it.
NOT_A_FINDING = frozenset({STATUS_ERROR})

# Reasons. The status answers "which bucket"; the reason answers "why", and
# is kept machine-readable so scoring can subdivide later without re-running.
REASON_HELD = "cluster_matched"
REASON_PAGE_ABSENT = "volume_held_page_absent"
REASON_REPORTER_ABSENT = "reporter_absent_from_courtlistener"
REASON_VOLUME_ABSENT = "volume_absent_from_courtlistener"
REASON_UNKNOWN_REPORTER = "reporter_not_a_known_us_reporter"
REASON_UNPARSEABLE = "unparseable_citation"
REASON_NOT_CASE_LAW = "not_a_case_citation"
REASON_COVERAGE_UNKNOWN = "coverage_probe_skipped"
REASON_MULTIPLE = "multiple_clusters_matched"
REASON_RATE_LIMITED = "rate_limited"
REASON_NETWORK = "network_error"
REASON_HTTP = "http_error"
REASON_NO_TOKEN = "no_api_token"

#: CourtListener has no Shepard's-style treatment signal. Say so in the data
#: rather than letting an empty history field read as "no negative history".
HISTORY_CAVEAT = (
    "CourtListener carries no Shepard's or KeyCite style treatment signal. "
    "These fields are whatever the source record contained and are often "
    "empty. An empty history is not a statement that the case is good law."
)


@dataclass
class Resolution:
    """One citation, one lookup, one outcome."""

    query: str
    status: str
    reason: str
    explanation: str
    retrieved_at_utc: str

    # As written in the citation under test.
    cited_volume: str | None = None
    cited_reporter: str | None = None
    cited_page: str | None = None
    cited_year: int | None = None
    cited_court: str | None = None
    normalized_citation: str | None = None

    # As held by CourtListener. Populated only when status is resolved.
    volume: str | None = None
    reporter: str | None = None
    page: str | None = None
    year: int | None = None
    court: str | None = None
    court_id: str | None = None
    case_name: str | None = None
    date_filed: str | None = None
    cluster_id: int | None = None
    courtlistener_url: str | None = None
    parallel_citations: list = field(default_factory=list)
    subsequent_history: dict | None = None

    # Where a claim of "not found" comes from, so it can be audited.
    coverage: dict | None = None

    # The citation resolved, but something in it does not match the case.
    discrepancies: list = field(default_factory=list)

    candidates: list = field(default_factory=list)
    from_cache: bool = False
    cached_at_utc: str | None = None

    @property
    def is_scorable(self) -> bool:
        return self.status in SCORABLE

    def as_dict(self) -> dict:
        return asdict(self)
