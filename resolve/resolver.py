"""The three-outcome resolution logic.

    citation-lookup returns a cluster        -> resolved
    citation-lookup returns 404              -> ask coverage, then decide
    citation-lookup returns nothing parseable-> not a US case citation
    the call failed                          -> error, which is not a finding

Only the second branch can produce `not_found`, and only after CourtListener
has confirmed it holds the cited volume. That confirmation is the whole point:
without it a coverage gap and a fabricated citation look identical.
"""

from __future__ import annotations

from . import citations as cite
from .client import CourtListener, MissingToken, RateLimited, ResolverError, TransportError, redact
from .config import Config
from .coverage import HELD, REPORTER_GAP, UNKNOWN, VOLUME_GAP, VOLUME_SPARSE, CoverageProbe
from .journal import iso_utc, utc_now
from .models import (
    HISTORY_CAVEAT,
    REASON_COVERAGE_UNKNOWN,
    REASON_HELD,
    REASON_HTTP,
    REASON_MULTIPLE,
    REASON_NETWORK,
    REASON_NO_TOKEN,
    REASON_NOT_CASE_LAW,
    REASON_PAGE_ABSENT,
    REASON_RATE_LIMITED,
    REASON_REPORTER_ABSENT,
    REASON_UNKNOWN_REPORTER,
    REASON_UNPARSEABLE,
    REASON_VOLUME_ABSENT,
    REASON_VOLUME_SPARSE,
    STATUS_AMBIGUOUS,
    STATUS_ERROR,
    STATUS_NOT_COVERED,
    STATUS_NOT_FOUND,
    STATUS_RESOLVED,
    Resolution,
)

# Per-citation status codes returned inside a citation-lookup response.
LOOKUP_OK = 200
LOOKUP_MULTIPLE = 300
LOOKUP_BAD = 400
LOOKUP_NOT_FOUND = 404


class Resolver:
    """Resolves citations against CourtListener, with cache and journal."""

    def __init__(self, client: CourtListener, config: Config | None = None,
                 cache=None, journal=None) -> None:
        self._client = client
        self._config = config or Config()
        self._cache = cache
        self._journal = journal
        self._probe = CoverageProbe(client, cache=cache)

    def resolve(self, text: str) -> Resolution:
        """Resolve one citation. Never raises for a lookup failure."""
        query = (text or "").strip()
        if not query:
            return self._finish(
                self._blank(query, STATUS_ERROR, REASON_UNPARSEABLE, "empty citation"),
                store=False,
            )

        if self._cache is not None and self._config.use_cache and not self._config.refresh:
            cached = self._cache.get(query)
            if cached is not None:
                return self._finish(cached, store=False)

        before = len(self._client.calls)
        try:
            resolution = self._lookup(query)
            store = True
        except RateLimited as exc:
            resolution = self._blank(query, STATUS_ERROR, REASON_RATE_LIMITED, redact(str(exc)))
            store = False
        except MissingToken as exc:
            resolution = self._blank(query, STATUS_ERROR, REASON_NO_TOKEN, redact(str(exc)))
            store = False
        except TransportError as exc:
            resolution = self._blank(query, STATUS_ERROR, REASON_NETWORK, redact(str(exc)))
            store = False
        except ResolverError as exc:
            resolution = self._blank(query, STATUS_ERROR, REASON_HTTP, redact(str(exc)))
            store = False

        return self._finish(resolution, store=store, calls=self._client.calls[before:])

    # -- internals ---------------------------------------------------------

    def _blank(self, query: str, status: str, reason: str, explanation: str) -> Resolution:
        return Resolution(
            query=query,
            status=status,
            reason=reason,
            explanation=explanation,
            retrieved_at_utc=iso_utc(utc_now()),
        )

    def _finish(self, resolution: Resolution, store: bool, calls=None) -> Resolution:
        if store and self._cache is not None and self._config.use_cache:
            self._cache.put(resolution)
        if self._journal is not None:
            self._journal.record(resolution, calls=calls)
        return resolution

    def _lookup(self, query: str) -> Resolution:
        parsed = cite.parse(query)

        # A statute or a law review article is not missing from a case-law
        # database; it was never in scope for one.
        non_case = cite.non_case_citation(query)
        if non_case and not parsed.is_complete:
            resolution = self._blank(
                query, STATUS_NOT_COVERED, REASON_NOT_CASE_LAW,
                f"reads as a citation to {non_case}, which CourtListener does "
                "not index; CourtListener holds court opinions",
            )
            self._stamp_cited(resolution, parsed)
            return resolution

        entries = self._client.lookup_citation(query)
        entry = self._pick(entries, parsed)

        if entry is None:
            return self._unparseable(query, parsed, non_case)

        status_code = entry.get("status")
        clusters = entry.get("clusters") or []

        if status_code == LOOKUP_OK and len(clusters) == 1:
            return self._resolved(query, parsed, entry, clusters[0])

        if status_code == LOOKUP_MULTIPLE or len(clusters) > 1:
            distinct = self._distinct_cases(clusters)
            if len(distinct) == 1:
                # The same case recorded twice in CourtListener is a database
                # duplicate, not two cases sharing a citation. Sending it to
                # attorney adjudication would inflate the unscorable bucket.
                resolved = self._resolved(query, parsed, entry, distinct[0])
                resolved.explanation = (
                    f"CourtListener holds the cited case in {len(clusters)} "
                    "duplicate records of the same decision"
                )
                resolved.candidates = self._candidates(clusters)
                return resolved
            return self._ambiguous(query, parsed, entry, clusters)

        if status_code == LOOKUP_NOT_FOUND or not clusters:
            return self._absent(query, parsed, entry)

        if status_code == LOOKUP_BAD:
            return self._unparseable(query, parsed, non_case, entry.get("error_message"))

        resolution = self._blank(
            query, STATUS_ERROR, REASON_HTTP,
            f"unexpected citation-lookup status {status_code!r}",
        )
        self._stamp_cited(resolution, parsed)
        return resolution

    @staticmethod
    def _pick(entries: list, parsed) -> dict | None:
        """Choose the entry matching the citation under test.

        A query string can contain more than one citation. Prefer the one
        whose normalized form matches what was parsed, else the first.
        """
        if not entries:
            return None
        if parsed.is_complete:
            wanted = f"{parsed.volume} {parsed.reporter} {parsed.page}".casefold()
            for entry in entries:
                for normalized in entry.get("normalized_citations") or []:
                    if normalized.casefold() == wanted:
                        return entry
        return entries[0]

    def _stamp_cited(self, resolution: Resolution, parsed) -> None:
        resolution.cited_volume = parsed.volume
        resolution.cited_reporter = parsed.reporter
        resolution.cited_page = parsed.page
        resolution.cited_year = parsed.year
        resolution.cited_court = parsed.court
        resolution.normalized_citation = parsed.normalized

    # -- outcomes ----------------------------------------------------------

    def _resolved(self, query: str, parsed, entry: dict, cluster: dict) -> Resolution:
        resolution = self._blank(
            query, STATUS_RESOLVED, REASON_HELD,
            "CourtListener holds the cited case",
        )
        self._stamp_cited(resolution, parsed)

        matched = self._matching_citation(cluster, parsed)
        date_filed = cluster.get("date_filed") or ""
        court_name, court_id = self._court(cluster)

        resolution.volume = (matched or {}).get("volume") or parsed.volume
        resolution.reporter = (matched or {}).get("reporter") or parsed.reporter
        resolution.page = (matched or {}).get("page") or parsed.page
        resolution.year = int(date_filed[:4]) if date_filed[:4].isdigit() else None
        resolution.court = court_name
        resolution.court_id = court_id
        resolution.case_name = cluster.get("case_name") or cluster.get("case_name_full")
        resolution.date_filed = date_filed or None
        resolution.cluster_id = cluster.get("id")
        absolute = cluster.get("absolute_url")
        resolution.courtlistener_url = (
            f"https://www.courtlistener.com{absolute}" if absolute else None
        )
        resolution.parallel_citations = [
            f"{c.get('volume')} {c.get('reporter')} {c.get('page')}"
            for c in cluster.get("citations") or []
        ]
        resolution.subsequent_history = self._history(cluster)
        resolution.discrepancies = self._discrepancies(parsed, resolution)
        return resolution

    @staticmethod
    def _matching_citation(cluster: dict, parsed) -> dict | None:
        for candidate in cluster.get("citations") or []:
            if (
                parsed.volume
                and str(candidate.get("volume")) == str(parsed.volume)
                and str(candidate.get("page")) == str(parsed.page)
            ):
                return candidate
        return None

    def _court(self, cluster: dict) -> tuple[str | None, str | None]:
        """Name the deciding court.

        A cluster carries only a docket id, so this costs one call for the
        docket and one for the court, the latter cached permanently since a
        court's name does not change. A failure here leaves the court unnamed
        and never downgrades an otherwise good resolution.
        """
        name = cluster.get("court") or cluster.get("court_name")
        identifier = cluster.get("court_id")
        if isinstance(name, dict):
            identifier = name.get("id") or identifier
            name = name.get("full_name") or name.get("short_name")
        if name and identifier:
            return name, identifier

        if not self._config.resolve_court:
            return name, identifier

        docket_id = cluster.get("docket_id")
        if not identifier and docket_id:
            try:
                identifier = self._client.docket(docket_id).get("court_id") or identifier
            except ResolverError:
                return name, identifier

        if not identifier:
            return name, identifier

        if self._cache is not None:
            cached = self._cache.get_court(identifier)
            if cached:
                return cached, identifier
        try:
            full_name = self._client.court(identifier).get("full_name")
        except ResolverError:
            return name, identifier
        if self._cache is not None and full_name:
            self._cache.put_court(identifier, full_name)
        return full_name or name, identifier

    @staticmethod
    def _history(cluster: dict) -> dict:
        return {
            "history": cluster.get("history") or None,
            "disposition": cluster.get("disposition") or None,
            "procedural_history": cluster.get("procedural_history") or None,
            "other_dates": cluster.get("other_dates") or None,
            "cross_reference": cluster.get("cross_reference") or None,
            "correction": cluster.get("correction") or None,
            "precedential_status": cluster.get("precedential_status") or None,
            "citation_count": cluster.get("citation_count"),
            "caveat": HISTORY_CAVEAT,
        }

    @staticmethod
    def _discrepancies(parsed, resolution: Resolution) -> list:
        """The cite resolves, but something in it does not match the case."""
        found = []
        if parsed.year and resolution.year and parsed.year != resolution.year:
            found.append(
                {
                    "field": "year",
                    "cited": parsed.year,
                    "actual": resolution.year,
                    "detail": (
                        f"citation says {parsed.year}; CourtListener has the "
                        f"case filed {resolution.date_filed}"
                    ),
                }
            )
        status = (resolution.subsequent_history or {}).get("precedential_status")
        if status and status not in ("Published", "Unknown"):
            found.append(
                {
                    "field": "precedential_status",
                    "cited": None,
                    "actual": status,
                    "detail": f"CourtListener marks this opinion {status}",
                }
            )
        return found

    @staticmethod
    def _distinct_cases(clusters: list) -> list:
        """Collapse records of the same decision.

        Same docket, or same case name on the same date, is one case however
        many times CourtListener has stored it.
        """
        seen = {}
        for cluster in clusters:
            key = cluster.get("docket_id") or (
                (cluster.get("case_name") or "").casefold(),
                cluster.get("date_filed"),
            )
            seen.setdefault(key, cluster)
        return list(seen.values())

    @staticmethod
    def _candidates(clusters: list) -> list:
        return [
            {
                "cluster_id": c.get("id"),
                "case_name": c.get("case_name"),
                "date_filed": c.get("date_filed"),
                "url": (
                    f"https://www.courtlistener.com{c.get('absolute_url')}"
                    if c.get("absolute_url") else None
                ),
            }
            for c in clusters[:10]
        ]

    def _ambiguous(self, query: str, parsed, entry: dict, clusters: list) -> Resolution:
        resolution = self._blank(
            query, STATUS_AMBIGUOUS, REASON_MULTIPLE,
            f"{len(clusters)} cases match this citation; an attorney resolves it",
        )
        self._stamp_cited(resolution, parsed)
        resolution.candidates = self._candidates(clusters)
        return resolution

    def _unparseable(self, query: str, parsed, non_case: str | None,
                     error_message: str | None = None) -> Resolution:
        """Nothing CourtListener recognises as a case citation.

        This is `not_covered`, not `not_found`. CourtListener indexes US
        court opinions; a string it cannot parse as one may be a foreign
        reporter, a specialty tribunal, or an invented reporter, and this
        tool cannot tell those apart. That judgement is an attorney's.
        """
        if non_case:
            explanation = (
                f"reads as a citation to {non_case}, which CourtListener does "
                "not index; CourtListener holds court opinions"
            )
            reason = REASON_NOT_CASE_LAW
        elif parsed.reporter and not parsed.reporter_is_known:
            explanation = (
                f"{parsed.reporter!r} is not a reporter abbreviation in "
                "reporters-db, so CourtListener cannot hold it. It may be a "
                "foreign or specialty reporter, or it may not exist; "
                "CourtListener cannot tell those apart"
            )
            reason = REASON_UNKNOWN_REPORTER
        else:
            explanation = (
                "CourtListener did not recognise this as a case citation"
                + (f": {error_message}" if error_message else "")
            )
            reason = REASON_UNPARSEABLE

        resolution = self._blank(query, STATUS_NOT_COVERED, reason, explanation)
        self._stamp_cited(resolution, parsed)
        return resolution

    def _absent(self, query: str, parsed, entry: dict) -> Resolution:
        """No case at that page. Coverage decides which finding this is."""
        reporter = parsed.canonical_reporter or parsed.reporter
        message = entry.get("error_message") or ""

        if not reporter:
            resolution = self._blank(
                query, STATUS_NOT_COVERED, REASON_UNPARSEABLE,
                "no reporter could be read from the citation, so coverage "
                "cannot be established",
            )
            self._stamp_cited(resolution, parsed)
            return resolution

        if not self._config.probe_coverage:
            resolution = self._blank(
                query, STATUS_NOT_COVERED, REASON_COVERAGE_UNKNOWN,
                "no case at this citation, and the coverage probe is disabled, "
                "so a coverage gap cannot be ruled out. Not scorable as a "
                "fabrication without the probe",
            )
            self._stamp_cited(resolution, parsed)
            resolution.coverage = {"verdict": UNKNOWN, "detail": "probe disabled"}
            return resolution

        verdict = self._probe.check(reporter, parsed.volume, parsed.page)

        if verdict.verdict == HELD:
            resolution = self._blank(
                query, STATUS_NOT_FOUND, REASON_PAGE_ABSENT,
                f"{verdict.detail}, but no case sits at page {parsed.page}"
                + (f" ({message})" if message else ""),
            )
        elif verdict.verdict == REPORTER_GAP:
            resolution = self._blank(
                query, STATUS_NOT_COVERED, REASON_REPORTER_ABSENT,
                f"{verdict.detail}, so its absence says nothing about the citation",
            )
        elif verdict.verdict == VOLUME_SPARSE:
            resolution = self._blank(
                query, STATUS_NOT_COVERED, REASON_VOLUME_SPARSE,
                f"{verdict.detail}. A page CourtListener never ingested and an "
                "invented page are indistinguishable from here",
            )
        elif verdict.verdict == VOLUME_GAP:
            resolution = self._blank(
                query, STATUS_NOT_COVERED, REASON_VOLUME_ABSENT,
                f"{verdict.detail}. A gap in CourtListener's holdings and an "
                "invented volume are indistinguishable from here",
            )
        else:
            resolution = self._blank(
                query, STATUS_NOT_COVERED, REASON_COVERAGE_UNKNOWN,
                f"no case at this citation and coverage could not be "
                f"established: {verdict.detail}",
            )

        self._stamp_cited(resolution, parsed)
        resolution.coverage = verdict.as_dict()
        return resolution
