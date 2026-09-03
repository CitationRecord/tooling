"""Hermetic tests for the citation resolver. No network, no API token.

    py -m pytest resolve/tests -q
"""

from __future__ import annotations

import json

import pytest

from resolve import TOKEN_ENV_VAR, USER_AGENT
from resolve.cache import Cache, citation_key
from resolve.citations import known_reporter, non_case_citation, parse
from resolve.client import MissingToken, RateLimited, redact, token
from resolve.config import Config
from resolve.coverage import HELD, REPORTER_GAP, VOLUME_GAP, CoverageProbe
from resolve.journal import Journal, iso_utc
from resolve.models import (
    NOT_A_FINDING,
    REASON_NOT_CASE_LAW,
    REASON_PAGE_ABSENT,
    REASON_REPORTER_ABSENT,
    REASON_UNKNOWN_REPORTER,
    REASON_VOLUME_ABSENT,
    SCORABLE,
    STATUS_AMBIGUOUS,
    STATUS_ERROR,
    STATUS_NOT_COVERED,
    STATUS_NOT_FOUND,
    STATUS_RESOLVED,
    UNSCORABLE,
    Resolution,
)
from resolve.resolver import Resolver


# --------------------------------------------------------------------------
# the distinction the whole component exists for


def test_not_found_and_not_covered_are_different_buckets():
    """A coverage gap must never be counted as a fabrication."""
    assert STATUS_NOT_FOUND in SCORABLE
    assert STATUS_NOT_COVERED in UNSCORABLE
    assert STATUS_NOT_COVERED not in SCORABLE
    assert STATUS_AMBIGUOUS not in SCORABLE


def test_an_error_is_not_a_finding_about_the_citation():
    assert STATUS_ERROR in NOT_A_FINDING
    assert STATUS_ERROR not in SCORABLE
    assert STATUS_ERROR not in UNSCORABLE


# --------------------------------------------------------------------------
# credentials


def test_token_read_from_environment_only(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV_VAR, "abcd1234efgh5678")
    assert token() == "abcd1234efgh5678"


def test_missing_token_raises_with_instructions(monkeypatch):
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)
    with pytest.raises(MissingToken) as excinfo:
        token()
    assert TOKEN_ENV_VAR in str(excinfo.value)
    assert "courtlistener.com/help/api" in str(excinfo.value)


def test_redact_scrubs_the_token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV_VAR, "supersecrettoken12345")
    token()
    assert "supersecrettoken12345" not in redact("failed with supersecrettoken12345 in url")
    assert "<redacted>" in redact("Authorization: Token supersecrettoken12345")


def test_no_token_flag_on_the_command_line():
    """A token passed as an argument would land in shell history."""
    from resolve import cli

    rendered = cli.build_parser().format_help()
    for banned in ("--token", "--api-key", "--api-token"):
        assert banned not in rendered


def test_user_agent_is_truthful():
    assert "CitationRecordResolver" in USER_AGENT
    assert "citationrecord.org" in USER_AGENT
    for impersonation in ("Mozilla", "Chrome/", "curl"):
        assert impersonation not in USER_AGENT


# --------------------------------------------------------------------------
# parsing


def test_parses_a_standard_citation():
    parsed = parse("576 U.S. 644")
    assert (parsed.volume, parsed.reporter, parsed.page) == ("576", "U.S.", "644")
    assert parsed.reporter_is_known
    assert parsed.is_complete


def test_parses_year_and_court_from_a_full_cite():
    parsed = parse("Obergefell v. Hodges, 576 U.S. 644 (2015)")
    assert parsed.year == 2015
    assert parsed.volume == "576"


def test_unknown_reporter_is_flagged_not_rejected():
    parsed = parse("123 Fake Rep. 456")
    assert parsed.reporter_is_known is False


@pytest.mark.parametrize(
    "text, label",
    [
        ("42 U.S.C. § 1983", "United States Code"),
        ("29 C.F.R. § 1910.1200", "Code of Federal Regulations"),
        ("88 Fed. Reg. 1234", "Federal Register"),
        ("101 Harv. L. Rev. 4", "law review"),
    ],
)
def test_non_case_authorities_are_recognised(text, label):
    assert non_case_citation(text) == label


def test_case_citation_is_not_mistaken_for_a_statute():
    assert non_case_citation("576 U.S. 644") is None


@pytest.mark.parametrize("variant", ["U.S.", "So. 3d", "So.3d", "F. Supp. 2d"])
def test_known_reporters_resolve(variant):
    known, canonical = known_reporter(variant)
    assert known
    assert canonical


def test_unknown_reporter_does_not_resolve():
    known, canonical = known_reporter("Totally Made Up Rptr.")
    assert not known and canonical is None


# --------------------------------------------------------------------------
# coverage probe


class FakeClient:
    """Counts search calls and answers from a fixed table."""

    def __init__(self, counts):
        self.counts = counts
        self.queries = []
        self.calls = []

    def search_count(self, query):
        self.queries.append(query)
        return self.counts.get(query, 0)


def probe_for(counts):
    return CoverageProbe(FakeClient(counts))


def test_volume_held_means_a_missing_page_is_a_real_absence():
    verdict = probe_for({'citation:("576 U.S.")': 31}).check("U.S.", "576")
    assert verdict.verdict == HELD
    assert verdict.volume_opinion_count == 31


def test_reporter_absent_means_coverage_gap():
    verdict = probe_for({}).check("Obscure Rep.", "12")
    assert verdict.verdict == REPORTER_GAP
    assert verdict.reporter_opinion_count == 0


def test_reporter_held_but_volume_absent_is_a_coverage_gap():
    verdict = probe_for({'citation:("U.S.")': 1367558}).check("U.S.", "9999")
    assert verdict.verdict == VOLUME_GAP


def test_coverage_counts_are_probed_once_per_book():
    client = FakeClient({'citation:("U.S.")': 10})
    probe = CoverageProbe(client)
    for _ in range(4):
        probe.check("U.S.", "9999")
    # One volume query and one reporter query, not four of each.
    assert len(client.queries) == 2


# --------------------------------------------------------------------------
# resolver, driven by canned API replies


class ScriptedClient:
    def __init__(self, entries, counts=None, cluster_court=None):
        self.entries = entries
        self.counts = counts or {}
        self.calls = []
        self._cluster_court = cluster_court

    def lookup_citation(self, text):
        return self.entries

    def search_count(self, query):
        return self.counts.get(query, 0)

    def docket(self, docket_id):
        return {"court_id": "scotus"}

    def court(self, court_id):
        return {"full_name": "Supreme Court of the United States"}


CLUSTER = {
    "id": 2812209,
    "case_name": "Obergefell v. Hodges",
    "date_filed": "2015-06-26",
    "absolute_url": "/opinion/2812209/obergefell-v-hodges/",
    "docket_id": 2668808,
    "precedential_status": "Published",
    "citation_count": 962,
    "citations": [{"volume": "576", "reporter": "U.S.", "page": "644"}],
    "history": "",
    "disposition": "",
}


def resolver_for(client, **config_kwargs):
    return Resolver(client, Config(**config_kwargs))


def test_resolved_returns_the_required_fields():
    client = ScriptedClient([{"status": 200, "clusters": [CLUSTER],
                             "normalized_citations": ["576 U.S. 644"]}])
    result = resolver_for(client).resolve("576 U.S. 644")
    assert result.status == STATUS_RESOLVED
    assert (result.volume, result.reporter, result.page) == ("576", "U.S.", "644")
    assert result.year == 2015
    assert result.court == "Supreme Court of the United States"
    assert result.subsequent_history["precedential_status"] == "Published"
    assert "Shepard" in result.subsequent_history["caveat"]


def test_missing_page_in_a_held_volume_is_not_found():
    client = ScriptedClient(
        [{"status": 404, "clusters": [], "error_message": "Citation not found",
          "normalized_citations": ["576 U.S. 9999"]}],
        counts={'citation:("576 U.S.")': 31},
    )
    result = resolver_for(client).resolve("576 U.S. 9999")
    assert result.status == STATUS_NOT_FOUND
    assert result.reason == REASON_PAGE_ABSENT
    assert result.is_scorable


def test_missing_volume_is_not_covered():
    client = ScriptedClient(
        [{"status": 404, "clusters": [], "normalized_citations": ["9999 U.S. 1"]}],
        counts={'citation:("U.S.")': 1367558},
    )
    result = resolver_for(client).resolve("9999 U.S. 1")
    assert result.status == STATUS_NOT_COVERED
    assert result.reason == REASON_VOLUME_ABSENT
    assert not result.is_scorable


def test_missing_reporter_is_not_covered():
    client = ScriptedClient(
        [{"status": 404, "clusters": [], "normalized_citations": ["5 Alaska Fed. 10"]}],
        counts={},
    )
    result = resolver_for(client).resolve("5 Alaska Fed. 10")
    assert result.status == STATUS_NOT_COVERED
    assert result.reason == REASON_REPORTER_ABSENT


def test_unknown_reporter_is_not_covered_not_not_found():
    """An invented reporter and an obscure real one are indistinguishable."""
    result = resolver_for(ScriptedClient([])).resolve("123 Fake Rep. 456")
    assert result.status == STATUS_NOT_COVERED
    assert result.reason == REASON_UNKNOWN_REPORTER


def test_statute_is_not_covered():
    result = resolver_for(ScriptedClient([])).resolve("42 U.S.C. § 1983")
    assert result.status == STATUS_NOT_COVERED
    assert result.reason == REASON_NOT_CASE_LAW


def test_multiple_matches_are_ambiguous_not_resolved():
    client = ScriptedClient([{"status": 300, "clusters": [CLUSTER, dict(CLUSTER, id=99)]}])
    result = resolver_for(client).resolve("576 U.S. 644")
    assert result.status == STATUS_AMBIGUOUS
    assert len(result.candidates) == 2
    assert not result.is_scorable


def test_disabling_the_probe_never_yields_not_found():
    """Without coverage evidence, a miss cannot be called a fabrication."""
    client = ScriptedClient([{"status": 404, "clusters": []}], counts={'citation:("576 U.S.")': 31})
    result = resolver_for(client, probe_coverage=False).resolve("576 U.S. 9999")
    assert result.status == STATUS_NOT_COVERED


class ThrottledClient(ScriptedClient):
    def lookup_citation(self, text):
        raise RateLimited("rate limit reached")


def test_rate_limit_is_an_error_never_a_missing_citation():
    result = resolver_for(ThrottledClient([])).resolve("576 U.S. 644")
    assert result.status == STATUS_ERROR
    assert not result.is_scorable


def test_year_mismatch_is_reported_as_a_discrepancy():
    client = ScriptedClient([{"status": 200, "clusters": [CLUSTER],
                              "normalized_citations": ["576 U.S. 644"]}])
    result = resolver_for(client).resolve("Obergefell v. Hodges, 576 U.S. 644 (2019)")
    assert result.status == STATUS_RESOLVED
    assert any(d["field"] == "year" for d in result.discrepancies)


# --------------------------------------------------------------------------
# cache


def sample(status=STATUS_RESOLVED, query="576 U.S. 644"):
    return Resolution(
        query=query, status=status, reason="r", explanation="e",
        retrieved_at_utc=iso_utc(),
    )


def test_cache_round_trips(tmp_path):
    with Cache(tmp_path / "c.sqlite3") as cache:
        cache.put(sample())
        loaded = cache.get("576 U.S. 644")
    assert loaded is not None
    assert loaded.from_cache is True
    assert loaded.cached_at_utc


def test_cache_key_ignores_case_and_spacing(tmp_path):
    with Cache(tmp_path / "c.sqlite3") as cache:
        cache.put(sample())
        assert cache.get("576  u.s.   644") is not None
    assert citation_key(" 576  U.S. 644 ") == "576 u.s. 644"


def test_errors_are_never_cached(tmp_path):
    """A throttle must not become a permanent finding."""
    with Cache(tmp_path / "c.sqlite3") as cache:
        assert cache.put(sample(status=STATUS_ERROR)) is False
        assert cache.get("576 U.S. 644") is None


def test_negative_results_expire_but_resolutions_do_not(tmp_path):
    with Cache(tmp_path / "c.sqlite3", negative_max_age_days=0) as cache:
        cache.put(sample(status=STATUS_NOT_FOUND, query="576 U.S. 9999"))
        cache.put(sample(status=STATUS_RESOLVED, query="576 U.S. 644"))
        assert cache.get("576 U.S. 9999") is None
        assert cache.get("576 U.S. 644") is not None


def test_coverage_counts_are_cached(tmp_path):
    with Cache(tmp_path / "c.sqlite3") as cache:
        cache.put_coverage("U.S.", "576", 31)
        cache.put_coverage("U.S.", None, 1367558)
        assert cache.get_coverage("U.S.", "576") == 31
        assert cache.get_coverage("U.S.", None) == 1367558
        assert cache.get_coverage("U.S.", "999") is None


# --------------------------------------------------------------------------
# journal


def test_every_lookup_is_logged_with_a_utc_timestamp(tmp_path):
    journal = Journal(tmp_path / "lookups.jsonl", {"resolver_version": "test"})
    journal.record(sample())
    journal.record(sample(status=STATUS_NOT_COVERED, query="9999 U.S. 1"))

    entries = list(journal.read())
    assert len(entries) == 2
    for entry in entries:
        assert entry["logged_at_utc"].endswith("Z")
        assert entry["retrieved_at_utc"].endswith("Z")
        assert "provenance" in entry


def test_journal_is_append_only(tmp_path):
    path = tmp_path / "lookups.jsonl"
    Journal(path, {}).record(sample())
    Journal(path, {}).record(sample(query="410 U.S. 113"))
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_journal_records_the_scorable_flag(tmp_path):
    journal = Journal(tmp_path / "lookups.jsonl", {})
    journal.record(sample(status=STATUS_NOT_COVERED))
    entry = next(iter(journal.read()))
    assert entry["scorable"] is False


def test_journal_never_writes_the_token(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV_VAR, "leakytoken1234567890")
    token()
    journal = Journal(tmp_path / "lookups.jsonl", {})
    leaky = sample()
    leaky.explanation = "failed using leakytoken1234567890"
    journal.record(leaky)
    body = (tmp_path / "lookups.jsonl").read_text(encoding="utf-8")
    assert "leakytoken1234567890" not in body
    assert "<redacted>" in body


def test_journal_skips_malformed_lines(tmp_path):
    path = tmp_path / "lookups.jsonl"
    journal = Journal(path, {})
    journal.record(sample())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("not json\n\n")
    journal.record(sample(query="410 U.S. 113"))
    assert len(list(journal.read())) == 2


def test_resolution_serialises(tmp_path):
    assert json.dumps(sample().as_dict())


# --------------------------------------------------------------------------
# cli plumbing


def test_batch_file_survives_a_byte_order_mark(tmp_path):
    """PowerShell writes UTF-8 with a BOM; it must not ride on citation one."""
    from resolve.cli import read_batch

    path = tmp_path / "cites.txt"
    path.write_text(
        "# tracked citations\n576 U.S. 644\n\n9999 U.S. 1\n",
        encoding="utf-8-sig",
    )
    assert read_batch(path) == ["576 U.S. 644", "9999 U.S. 1"]


def test_run_context_goes_to_stderr_so_json_stays_parseable(capsys):
    """--json must leave stdout valid JSON with nothing prepended."""
    from resolve.cli import _note, _out

    _note("api https://example.invalid")
    _out(json.dumps([{"status": "resolved"}]))
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [{"status": "resolved"}]
    assert "api" in captured.err
