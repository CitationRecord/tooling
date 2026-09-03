"""Hermetic tests for the claim archiver. No network, no browser.

    py -m pytest archive/tests -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from archive import SCHEMA, USER_AGENT
from archive.capture import (
    CHANGE_DIFFERENT,
    CHANGE_FIRST,
    CHANGE_SAME,
    STATUS_UNVERIFIED,
    RunSummary,
    _change_block,
    _tally,
    capture_claim,
    sha256_text,
)
from archive.claims import Claim, ClaimsError, load_claims
from archive.config import Config
from archive.manifest import (
    append,
    last_capture_by_claim,
    last_record_by_claim,
    records,
    write_once,
)
from archive.wayback import _snapshot_from

MINIMAL = """
version: 1
methodology_version: "1.0"
claims:
  - id: vendor-one
    url: https://vendor.example/product
    verified: true
    claim: "Zero hallucinations."
"""

UNVERIFIED = """
version: 1
claims:
  - id: vendor-two
    url: null
    verified: false
    claim: "Recorded from an ad; primary source not located."
"""


def write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# identity


def test_user_agent_is_truthful():
    """The archiver identifies itself as itself. Design constraint, not style."""
    assert "CitationRecordArchiver" in USER_AGENT
    assert "citationrecord.org" in USER_AGENT
    for impersonation in ("Mozilla", "Chrome/", "Safari", "Gecko"):
        assert impersonation not in USER_AGENT


def test_no_robots_override_exists():
    """There must be no flag that lets a run ignore robots.txt."""
    from archive import cli

    parser = cli.build_parser()
    rendered = parser.format_help()
    for sub in ("capture",):
        rendered += parser._subparsers._group_actions[0].choices[sub].format_help()
    for banned in ("--ignore-robots", "--force", "--no-robots"):
        assert banned not in rendered


# --------------------------------------------------------------------------
# claim file


def test_loads_minimal_claim_file(tmp_path):
    claim_set = load_claims(write(tmp_path, "claims.yaml", MINIMAL))
    assert len(claim_set.claims) == 1
    assert claim_set.methodology_version == "1.0"
    assert len(claim_set.file_sha256) == 64
    assert claim_set.claims[0].origin == "https://vendor.example"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ("version: 1\nclaims: []\n", "non-empty list"),
        ("version: 2\nclaims:\n  - {id: a, url: 'https://a.example/', verified: true, claim: c}\n", "version"),
        ("version: 1\nclaims:\n  - {url: 'https://a.example/', verified: true, claim: c}\n", "'id' is required"),
        ("version: 1\nclaims:\n  - {id: a, verified: true, url: 'https://a.example/'}\n", "'claim' is required"),
        ("version: 1\nclaims:\n  - {id: a, url: 'ftp://a.example/', verified: true, claim: c}\n", "http(s)"),
        ("version: 1\nclaims:\n  - {id: 'a/b', url: 'https://a.example/', verified: true, claim: c}\n", "must be 1-64"),
        ("version: 1\nclaims:\n  - {id: a, url: 'https://a.example/', verified: true, claim: c, oops: 1}\n", "unknown key"),
        (
            "version: 1\nclaims:\n"
            "  - {id: a, url: 'https://a.example/1', verified: true, claim: c}\n"
            "  - {id: a, url: 'https://a.example/2', verified: true, claim: d}\n",
            "duplicate claim id",
        ),
        # An omitted flag is an error, never silent permission to fetch.
        ("version: 1\nclaims:\n  - {id: a, url: 'https://a.example/', claim: c}\n", "'verified' is required"),
        ("version: 1\nclaims:\n  - {id: a, url: 'https://a.example/', verified: 'yes', claim: c}\n", "'verified' is required"),
        # A confirmed entry has to say where the claim lives.
        ("version: 1\nclaims:\n  - {id: a, url: null, verified: true, claim: c}\n", "'url' is required"),
        # A URL that is present is validated even when unconfirmed.
        ("version: 1\nclaims:\n  - {id: a, url: 'not a url', verified: false, claim: c}\n", "http(s)"),
    ],
)
def test_rejects_malformed_claim_files(tmp_path, body, fragment):
    with pytest.raises(ClaimsError) as excinfo:
        load_claims(write(tmp_path, "claims.yaml", body))
    assert fragment in str(excinfo.value)


def test_unverified_entry_loads_without_a_url(tmp_path):
    """A claim whose source has not been located is still tracked, not fetched."""
    claim_set = load_claims(write(tmp_path, "claims.yaml", UNVERIFIED))
    claim = claim_set.claims[0]
    assert claim.verified is False
    assert claim.url is None
    assert claim.capturable is False
    assert claim.origin == ""


def test_verified_entry_with_a_url_is_capturable(tmp_path):
    claim_set = load_claims(write(tmp_path, "claims.yaml", MINIMAL))
    assert claim_set.claims[0].capturable is True


def test_select_rejects_unknown_id(tmp_path):
    claim_set = load_claims(write(tmp_path, "claims.yaml", MINIMAL))
    assert len(claim_set.select(())) == 1
    assert len(claim_set.select(("vendor-one",))) == 1
    with pytest.raises(ClaimsError):
        claim_set.select(("nope",))


def test_claim_text_is_hashed():
    claim = Claim(id="a", url="https://a.example/", claim_text="Zero hallucinations.", verified=True)
    assert claim.claim_text_sha256 == sha256_text("Zero hallucinations.")


# --------------------------------------------------------------------------
# change detection


def claim_at(url="https://vendor.example/product"):
    return Claim(id="vendor-one", url=url, claim_text="Zero hallucinations.", verified=True)


def test_first_capture_has_no_baseline():
    block = _change_block(claim_at(), None, "aaa", "bbb")
    assert block["content"] == CHANGE_FIRST
    assert block["text"] == CHANGE_FIRST


def test_identical_hash_is_unchanged():
    previous = {"content_sha256": "aaa", "text_sha256": "bbb", "record_id": "r1"}
    block = _change_block(claim_at(), previous, "aaa", "bbb")
    assert block["content"] == CHANGE_SAME
    assert block["text"] == CHANGE_SAME
    assert block["compared_to_record_id"] == "r1"


def test_different_hash_is_flagged():
    previous = {"content_sha256": "aaa", "text_sha256": "bbb"}
    block = _change_block(claim_at(), previous, "zzz", "bbb")
    assert block["content"] == CHANGE_DIFFERENT
    assert block["previous_content_sha256"] == "aaa"
    # Markup moved but the words on the page did not. Worth distinguishing:
    # most HTML churn is nonces and build ids, not edited claims.
    assert block["text"] == CHANGE_SAME


def test_url_change_is_noted():
    previous = {"content_sha256": "aaa", "url": "https://vendor.example/old"}
    block = _change_block(claim_at(), previous, "zzz", "ccc")
    assert block["url_changed_since"] == "https://vendor.example/old"


# --------------------------------------------------------------------------
# unverified sources are never fetched


class ExplodingBrowser:
    """Any page request at all is a test failure."""

    def capture(self, *args, **kwargs):
        raise AssertionError("an unverified claim must never be fetched")


class ExplodingRobots:
    def check(self, *args, **kwargs):
        raise AssertionError("an unverified claim must not even trigger a robots.txt request")


def unverified_claim(url=None):
    return Claim(
        id="from-an-ad",
        url=url,
        claim_text="Recorded from an ad; primary source not located.",
        verified=False,
    )


def refuse(tmp_path, claim):
    return capture_claim(
        claim=claim,
        config=Config(claims_path=tmp_path / "c.yaml", out_dir=tmp_path).resolved(),
        browser=ExplodingBrowser(),
        robots=ExplodingRobots(),
        provenance={},
        previous=None,
    )


@pytest.mark.parametrize("url", [None, "https://vendor.example/guessed"])
def test_unverified_claim_is_never_requested(tmp_path, url):
    record = refuse(tmp_path, unverified_claim(url))
    assert record["status"] == STATUS_UNVERIFIED
    assert record["verified_source"] is False
    assert record["content_sha256"] is None
    assert record["robots"]["checked"] is False
    assert record["wayback"]["status"] == "skipped"
    assert "paths" not in record
    assert not list(tmp_path.glob("snapshots/**/*"))


def test_unverified_claim_still_records_the_claim_text(tmp_path):
    """The entry stays visible in the manifest; only the fetch is withheld."""
    record = refuse(tmp_path, unverified_claim())
    assert record["claim_text"] == "Recorded from an ad; primary source not located."
    assert record["claim_text_sha256"]


def test_unverified_entries_hold_the_exit_code_at_one():
    summary = RunSummary()
    _tally(summary, {"status": STATUS_UNVERIFIED, "change": {}, "wayback": {}})
    assert summary.unverified == 1
    assert summary.captured == 0
    assert summary.exit_code == 1


# --------------------------------------------------------------------------
# manifest


def test_manifest_is_append_only(tmp_path):
    path = tmp_path / "manifest.jsonl"
    append(path, {"schema": SCHEMA, "claim_id": "a", "content_sha256": "1"})
    append(path, {"schema": SCHEMA, "claim_id": "a", "content_sha256": "2"})
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2
    assert [r["content_sha256"] for r in records(path)] == ["1", "2"]


def test_malformed_lines_are_skipped(tmp_path):
    path = tmp_path / "manifest.jsonl"
    append(path, {"claim_id": "a", "content_sha256": "1"})
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{ this is not json\n\n")
    append(path, {"claim_id": "a", "content_sha256": "2"})
    assert len(records(path)) == 2


def test_last_capture_ignores_blocked_records(tmp_path):
    """A refusal is recorded but is not a hash baseline."""
    path = tmp_path / "manifest.jsonl"
    append(path, {"claim_id": "a", "content_sha256": "1", "record_id": "r1"})
    append(path, {"claim_id": "a", "content_sha256": None, "status": "blocked_by_robots"})
    assert last_capture_by_claim(path)["a"]["record_id"] == "r1"
    assert last_record_by_claim(path)["a"]["status"] == "blocked_by_robots"


def test_missing_manifest_reads_as_empty(tmp_path):
    assert records(tmp_path / "absent.jsonl") == []


def test_snapshot_files_are_write_once(tmp_path):
    path = write_once(tmp_path / "snap" / "page.html", b"<html></html>")
    assert path.read_bytes() == b"<html></html>"
    with pytest.raises(FileExistsError):
        write_once(path, b"tampered")


def test_record_round_trips_as_json(tmp_path):
    path = tmp_path / "manifest.jsonl"
    record = {"claim_text": "café — 100% accurate", "claim_id": "a"}
    append(path, record)
    assert json.loads(path.read_text(encoding="utf-8"))["claim_text"] == record["claim_text"]


# --------------------------------------------------------------------------
# wayback URL parsing


@pytest.mark.parametrize(
    "candidate, expected_timestamp",
    [
        ("/web/20260903000043/https://example.com/", "20260903000043"),
        ("https://web.archive.org/web/20260903000043/https://example.com/", "20260903000043"),
        ("https://web.archive.org/web/20260903000043id_/https://example.com/", "20260903000043"),
    ],
)
def test_parses_snapshot_urls(candidate, expected_timestamp):
    url, timestamp = _snapshot_from(candidate)
    assert timestamp == expected_timestamp
    assert url.startswith("https://web.archive.org/web/")


@pytest.mark.parametrize("candidate", [None, "", "https://web.archive.org/save/https://example.com/"])
def test_rejects_non_snapshot_urls(candidate):
    assert _snapshot_from(candidate) is None


# --------------------------------------------------------------------------
# summary and exit codes


def summary_for(*statuses):
    summary = RunSummary()
    for status, change in statuses:
        _tally(summary, {"status": status, "change": {"content": change}, "wayback": {}})
    return summary


def test_exit_code_zero_when_nothing_moved():
    assert summary_for(("captured", CHANGE_SAME)).exit_code == 0


def test_exit_code_two_on_change():
    assert summary_for(("captured", CHANGE_DIFFERENT)).exit_code == 2


def test_exit_code_one_on_failure_takes_precedence():
    assert summary_for(("captured", CHANGE_DIFFERENT), ("fetch_error", None)).exit_code == 1
    assert summary_for(("blocked_by_robots", None)).exit_code == 1


# --------------------------------------------------------------------------
# config


def test_output_defaults_outside_the_repository():
    from archive.config import DEFAULT_OUT_DIR, REPO_ROOT

    assert REPO_ROOT not in DEFAULT_OUT_DIR.parents
    assert DEFAULT_OUT_DIR.name == "claim-archive"


def test_config_resolves_paths(tmp_path):
    config = Config(claims_path=Path("claims.yaml"), out_dir=tmp_path / "out").resolved()
    assert config.claims_path.is_absolute()
    assert config.manifest_path.name == "manifest.jsonl"
    assert config.snapshot_root.name == "snapshots"
