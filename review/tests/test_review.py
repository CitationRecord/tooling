"""Hermetic tests for the review packet. No network.

    py -m pytest review/tests -q
"""

from __future__ import annotations

import json

import pytest

from register.cli import refuses_registration
from review import REGISTRABLE_REASON, SCHEMA, WARNING
from review.packet import (
    Unsourced,
    authority,
    item,
    packet,
    unsourced,
    unsourced_packet,
    write,
)
from sample.exclude import load_all

CASE = dict(kind="case", name="Doe v. Roe, 1 F.3d 1",
            quote="the rule is stated here", path="resolve",
            cite="1 F.3d 1", cluster_id="7")
RULE = dict(kind="local rule", name="Civil Local Rule 7-3",
            quote="an opposition must be filed within 14 days",
            path="archive", url="https://example.invalid/lr7-3",
            sha256="0" * 64, retrieved_at_utc="2026-09-13T00:00:00Z")


def an_item(**over):
    base = dict(
        item_id="rev-001", tier="document-sourced", category="circuit-split",
        question="In the Ninth Circuit, what is the rule?",
        answer="The rule is X.", confidence="reading",
        authority_record=authority(**CASE),
        question_source={"where": "a named article", "url": "https://x.invalid"},
        drafted_by="JB Wagoner, not an attorney")
    base.update(over)
    return item(**base)


# --------------------------------------------------------------------------
# an answer without an authority is not an item


def test_an_item_with_no_authority_is_refused():
    with pytest.raises(Unsourced):
        an_item(authority_record=None)


def test_an_authority_with_no_quoted_passage_is_refused():
    """A reviewer asked whether an authority says what an answer claims needs
    the words, not a pointer to go and find them."""
    with pytest.raises(Unsourced):
        authority(**{**CASE, "quote": "   "})


def test_an_unsourced_question_is_recorded_rather_than_dropped():
    entry = unsourced("Some question?", "local-rules",
                      looked_for="the district's published rules",
                      why_not="no rule on the point",
                      reason_kind="no authority found")
    assert entry["why_not"]
    assert entry["recorded_at_utc"]


def test_the_unsourced_reason_distinguishes_the_law_from_us():
    """A retrieval failure must not masquerade as a finding about the law."""
    ours = unsourced("Q?", "local-rules", looked_for="the court's PDF",
                     why_not="the court serves rules as script-rendered HTML "
                             "and no primary copy could be extracted",
                     reason_kind="authority not obtainable")
    theirs = unsourced("Q?", "local-rules", looked_for="the district's rules",
                       why_not="the district has no rule on the point",
                       reason_kind="no authority found")
    assert ours["reason_kind"] != theirs["reason_kind"]
    with pytest.raises(ValueError):
        unsourced("Q?", "local-rules", looked_for="x", why_not="y",
                  reason_kind="could not be bothered")


# --------------------------------------------------------------------------
# the tiers and their obligations


def test_an_interpretation_item_must_say_what_it_is_unsure_of():
    """An unhedged interpretation reads as a fact."""
    with pytest.raises(ValueError):
        an_item(tier="interpretation-dependent", category="doctrine",
                confidence="interpretation", hedges=[])


def test_an_interpretation_item_with_hedges_is_allowed():
    built = an_item(tier="interpretation-dependent", category="doctrine",
                    confidence="interpretation",
                    hedges=["the boundary with the adjacent doctrine is mine"])
    assert built["drafted_answer"]["hedges"]


def test_the_confidence_values_are_closed():
    with pytest.raises(ValueError):
        an_item(confidence="fairly sure")


def test_both_provenance_paths_are_accepted():
    assert authority(**CASE)["provenance_path"] == "resolve"
    assert authority(**RULE)["provenance_path"] == "archive"


def test_an_unknown_provenance_path_is_refused():
    with pytest.raises(ValueError):
        authority(**{**CASE, "path": "vibes"})


# --------------------------------------------------------------------------
# the warning, at every level


def test_the_warning_is_on_the_file_and_on_every_item():
    doc = packet("2026.Q4", "JB Wagoner", [an_item()], [], "unsourced.json")
    assert doc["warning"] == WARNING
    assert all(i["warning"] == WARNING for i in doc["items"])


def test_the_warning_says_what_a_wrong_answer_would_cause():
    assert "hallucinating when it answered correctly" in WARNING


def test_every_item_carries_an_empty_verdict_and_reviewer():
    built = an_item()
    assert built["verdict"] == {"assessment": None, "notes": None}
    assert built["reviewer"] == {"name": None, "date": None, "credit": None}


# --------------------------------------------------------------------------
# the packet refuses to be registered


def test_the_packet_declares_itself_unregistrable():
    doc = packet("2026.Q4", "JB Wagoner", [an_item()], [], "unsourced.json")
    assert doc["registrable"] is False
    assert doc["registrable_reason"] == REGISTRABLE_REASON
    assert doc["schema"] == SCHEMA


def test_register_refuses_the_packet(tmp_path):
    """The contract is general: register reads the flag, not the schema."""
    path = tmp_path / "packet.json"
    doc = packet("2026.Q4", "JB Wagoner", [an_item()], [], "unsourced.json")
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert refuses_registration(path) == REGISTRABLE_REASON


def test_register_does_not_refuse_an_ordinary_artifact(tmp_path):
    path = tmp_path / "ordinary.json"
    path.write_text(json.dumps({"queries": []}), encoding="utf-8")
    assert refuses_registration(path) is None


def test_the_unsourced_list_also_refuses_registration():
    doc = unsourced_packet("2026.Q4", [])
    assert doc["registrable"] is False


# --------------------------------------------------------------------------
# exclusion by topic, which is the key these categories collide on


def test_stanford_local_rule_and_circuit_topics_are_excluded():
    entry = load_all()[0]
    for question in (
        "In the U.S. District Court for the Southern District of Indiana, how "
        "many days before serving a Rule 45 subpoena must notice be served?",
        "In the Sixth Circuit, must the prosecution prove intent for harboring "
        "under 8 U.S.C. 1324?",
        "In the Eighth Circuit, can a private litigant sue under Section 2 of "
        "the Voting Rights Act?",
        "What is the near miss doctrine?",
    ):
        assert entry.excludes_topic(question)


def test_an_unrelated_question_on_the_same_shape_is_not_excluded():
    entry = load_all()[0]
    assert not entry.excludes_topic(
        "In the Northern District of California, how many days before a "
        "hearing must an opposition be filed?")
    assert not entry.excludes_topic(
        "In the Ninth Circuit, what is the standard for qualified immunity?")


def test_the_topic_coverage_is_stated_in_the_list():
    entry = load_all()[0]
    assert len(entry.topics) == 4
    assert "PARTIAL" in entry.coverage


# --------------------------------------------------------------------------
# where it may be written


def test_a_packet_may_not_be_written_into_a_repository(tmp_path):
    from sample.config import UnsafeLocation

    repo = tmp_path / "somewhere"
    (repo / ".git").mkdir(parents=True)
    doc = packet("2026.Q4", "JB Wagoner", [an_item()], [], "unsourced.json")
    with pytest.raises(UnsafeLocation):
        write(doc, repo / "packet.json")


# --------------------------------------------------------------------------
# resolving an unsourced entry, and the difference between sourced and retired


def _entry():
    from review.packet import unsourced

    return unsourced(
        question="When is an opposition due in N.D. Cal.?",
        category="local-rules",
        looked_for="Civil Local Rule 7-3",
        why_not="Two retrievals returned two different rules.",
        reason_kind="authority ambiguous")


AUTH = {"name": "N.D. Cal. Civil L.R. 7-3(a)",
        "url": "https://cand.uscourts.gov/x.pdf",
        "sha256": "a" * 64,
        "retrieved_at_utc": "2026-09-21"}


def test_a_resolution_carries_the_original_account_whole():
    """The list must still show how long the gap was open and what was tried."""
    from review.packet import resolved

    r = resolved(_entry(), "retired", AUTH, "burned by publication")
    assert r["was_unsourced"]["why_not"].startswith("Two retrievals")
    assert r["was_unsourced"]["reason_kind"] == "authority ambiguous"
    assert r["authority"]["sha256"] == "a" * 64


def test_retired_is_not_the_same_as_sourced():
    from review.packet import RESOLUTION_KINDS, resolved

    assert set(RESOLUTION_KINDS) == {"sourced", "retired"}
    assert resolved(_entry(), "retired", AUTH, "n")["resolution_kind"] == "retired"
    with pytest.raises(ValueError, match="resolution_kind"):
        resolved(_entry(), "found", AUTH, "n")


def test_a_resolution_without_a_pinned_authority_is_refused():
    """The same rule the packet applies to items: no authority, not an item."""
    from review.packet import Unsourced, resolved

    with pytest.raises(Unsourced):
        resolved(_entry(), "sourced", {}, "n")
    for missing in ("url", "sha256", "retrieved_at_utc"):
        partial = {k: v for k, v in AUTH.items() if k != missing}
        with pytest.raises(Unsourced, match=missing):
            resolved(_entry(), "sourced", partial, "n")


def test_a_resolution_must_say_what_it_means_for_the_edition():
    from review.packet import resolved

    with pytest.raises(ValueError, match="what it means"):
        resolved(_entry(), "retired", AUTH, "   ")


def test_the_packet_counts_retirements_separately():
    from review.packet import resolved, unsourced_packet

    r = resolved(_entry(), "retired", AUTH, "burned")
    doc = unsourced_packet("2026.Q4", [], [r])
    assert doc["counts"] == {"unsourced": 0, "resolved": 1, "retired": 1}
    assert doc["registrable"] is False
    assert "retired" in doc["resolved_note"]


def test_an_empty_resolved_list_is_the_old_shape_plus_zeroes():
    from review.packet import unsourced_packet

    doc = unsourced_packet("2026.Q4", [_entry()])
    assert doc["counts"] == {"unsourced": 1, "resolved": 0, "retired": 0}
    assert doc["resolved"] == []
