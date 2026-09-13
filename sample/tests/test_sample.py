"""Hermetic tests for query selection. No network, no bulk data.

    py -m pytest sample/tests -q
"""

from __future__ import annotations

import json

import pytest

from register.config import UnsafeLocation
from sample import DRAW_KEY, METADATA_KINDS
from sample.cli import METADATA_PLAN, main
from sample.config import workdir
from sample.draw import Draw, draw_key, new_seed, ordered, select
from sample.exclude import ExclusionList, load, load_all, normalise
from sample.negate import negate, reject_reason
from sample.queryset import ACCEPTABLE_RESPONSES, metadata_query


# --------------------------------------------------------------------------
# determinism


def test_the_draw_order_is_stable_across_input_order():
    items = [{"id": str(i)} for i in range(50)]
    forward = ordered(items, "seed", "cat", lambda c: c["id"])
    backward = ordered(list(reversed(items)), "seed", "cat", lambda c: c["id"])
    assert [c["id"] for c in forward] == [c["id"] for c in backward]


def test_a_different_seed_gives_a_different_order():
    items = [{"id": str(i)} for i in range(50)]
    one = [c["id"] for c in ordered(items, "seed-a", "cat", lambda c: c["id"])]
    two = [c["id"] for c in ordered(items, "seed-b", "cat", lambda c: c["id"])]
    assert one != two


def test_a_different_category_gives_a_different_order():
    items = [{"id": str(i)} for i in range(50)]
    one = [c["id"] for c in ordered(items, "s", "metadata:year",
                                    lambda c: c["id"])]
    two = [c["id"] for c in ordered(items, "s", "metadata:author",
                                    lambda c: c["id"])]
    assert one != two


def test_the_key_is_recomputable_from_its_published_description():
    import hashlib
    expected = hashlib.sha256(b"abc:metadata:42").hexdigest()
    assert draw_key("abc", "metadata", 42) == expected
    assert "sha256" in DRAW_KEY


def test_a_seed_is_thirty_two_bytes():
    assert len(bytes.fromhex(new_seed())) == 32


# --------------------------------------------------------------------------
# rejections are part of the record


def test_every_rejection_is_recorded_with_its_rule():
    items = [{"id": str(i)} for i in range(20)]
    result = select(items, "seed", "cat", 3, lambda c: c["id"],
                    check=lambda c: "too even" if int(c["id"]) % 2 == 0 else None)
    assert len(result.taken) == 3
    assert result.rejected
    assert all(r["rule"] == "too even" for r in result.rejected)
    assert all(int(r["candidate_id"]) % 2 == 0 for r in result.rejected)


def test_the_walk_reproduces_with_the_same_seed():
    items = [{"id": str(i)} for i in range(40)]
    check = lambda c: "skip" if int(c["id"]) % 3 else None
    first = select(items, "s", "cat", 4, lambda c: c["id"], check)
    again = select(items, "s", "cat", 4, lambda c: c["id"], check)
    assert [c["id"] for c in first.taken] == [c["id"] for c in again.taken]
    assert first.rejected == again.rejected


def test_an_exhausted_pool_reports_an_incomplete_draw():
    items = [{"id": "1"}, {"id": "2"}]
    result = select(items, "s", "cat", 5, lambda c: c["id"])
    assert not result.complete
    assert result.as_dict()["taken"] == 2


# --------------------------------------------------------------------------
# negation
#
# The risk: a holding negated sloppily is merely different rather than
# opposite, and a system that then finds a real supporting case is not wrong.


def test_a_simple_negator_is_removed():
    result = negate("holding that the statute does not apply to municipalities "
                    "organised under the general law")
    assert result.negated == ("the statute does apply to municipalities "
                              "organised under the general law")
    assert result.rule == "drop-not-after-auxiliary"


def test_cannot_becomes_can():
    result = negate("holding that a pro se litigant cannot recover attorney "
                    "fees under the statute at issue here")
    assert result.negated.startswith("a pro se litigant can recover")
    assert result.rule == "cannot-to-can"


def test_a_positive_holding_is_rejected_rather_than_negated():
    text = ("holding that the statute applies to municipalities and to their "
            "contractors acting within the scope of the agreement")
    assert reject_reason(text) == "no removable negator"
    assert negate(text) is None


def test_two_negators_are_rejected_for_ambiguous_scope():
    text = ("holding that the statute does not apply where the party has not "
            "exhausted the remedies available to it")
    assert reject_reason(text) == "more than one negator, scope ambiguous"


def test_surviving_negation_is_rejected():
    text = ("holding that the plaintiff does not prevail where the record "
            "lacks evidence of the required intent to deceive")
    assert reject_reason(text) == "negation survives the removal"


def test_a_second_clause_is_rejected():
    text = ("holding that the rule does not apply here; the parties agreed "
            "otherwise in writing before the dispute arose")
    assert reject_reason(text) == "more than one clause"


def test_length_bounds_are_enforced():
    """The floor is a pool filter, not an accident: a holding too short to
    state its own scope is too short to negate into a checkable opposite."""
    assert reject_reason("holding that it does not apply") == \
        "outside the length band"
    assert reject_reason(
        "holding that the statute does not apply to municipalities"
    ) == "outside the length band"


def test_a_non_holding_parenthetical_is_rejected():
    text = ("noting that the statute does not apply to municipalities or to "
            "their contractors under the agreement")
    assert reject_reason(text) == "not a holding-that parenthetical"


def test_the_rule_is_recorded_on_the_result():
    result = negate("holding that the doctrine does not extend to claims "
                    "arising under the later agreement")
    assert result.as_dict()["rule"] in {"drop-not-after-auxiliary",
                                        "cannot-to-can"}
    assert result.as_dict()["original"].startswith("holding that")


# --------------------------------------------------------------------------
# the exclusion list


def test_the_shipped_list_says_it_is_partial():
    lists = load_all()
    assert lists, "the Stanford list should ship with the component"
    entry = lists[0]
    assert entry.items_total == 202
    assert entry.items_covered < entry.items_total
    assert "PARTIAL" in entry.coverage


def test_coverage_travels_in_the_serialised_form():
    entry = load_all()[0]
    body = entry.as_dict()
    assert body["items_covered"] == 15
    assert body["items_total"] == 202
    assert 0 < body["coverage_fraction"] < 1


def test_a_listed_case_is_excluded_however_it_is_spelled():
    entry = load_all()[0]
    assert entry.excludes_name("Sears, Roebuck & Co. v. Blade")
    assert entry.excludes_name("Sears Roebuck and Co v Blade")
    assert entry.excludes_name("SEARS, ROEBUCK & CO. VS. BLADE")


def test_an_unlisted_case_is_not_excluded():
    entry = load_all()[0]
    assert not entry.excludes_name("Wagoner v. Some Unremarkable Defendant")


def test_a_listed_citation_is_excluded():
    entry = load_all()[0]
    assert entry.excludes_citation("315 F. Supp. 841")


def test_the_reason_names_the_source(tmp_path):
    entry = load_all()[0]
    why = entry.reason(name="In Re Bebar")
    assert why and "Magesh" in why


def test_normalisation_folds_the_v_forms():
    assert normalise("Smith v. Jones") == normalise("Smith vs Jones")
    assert normalise("Smith V. Jones") == normalise("Smith v Jones")


# --------------------------------------------------------------------------
# the artifact


def test_the_three_branch_response_is_recorded_with_its_source():
    assert len(ACCEPTABLE_RESPONSES["branches"]) == 3
    assert "Magesh" in ACCEPTABLE_RESPONSES["source"]
    assert "supersedes" in ACCEPTABLE_RESPONSES["branches"][2]


def test_the_metadata_plan_is_two_authorship_one_year_one_citation():
    assert dict(METADATA_PLAN) == {"author": 2, "year": 1, "citation": 1}
    assert sum(n for _, n in METADATA_PLAN) == 4
    assert set(dict(METADATA_PLAN)) <= set(METADATA_KINDS)


def test_a_metadata_query_carries_its_subject_and_no_answer_yet():
    candidate = {"cluster_id": "99", "case_name": "Doe v. Roe",
                 "reporter": "F. Supp.", "volume": "300", "page": "1",
                 "year": "1994", "citation_count": 0}
    query = metadata_query("year", candidate)
    assert "Doe v. Roe" in query["text"]
    assert query["ground_truth"] is None
    assert query["subject"]["cluster_id"] == "99"


# --------------------------------------------------------------------------
# the destination guard


def test_a_draft_may_not_be_written_into_any_repository(tmp_path):
    repo = tmp_path / "anything"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(UnsafeLocation):
        workdir(repo / "drafts")


def test_a_draft_directory_outside_every_repository_is_allowed(tmp_path):
    created = workdir(tmp_path / "query-drafts")
    assert created.is_dir()


def test_drawing_without_a_pool_says_which_one_is_missing(tmp_path, capsys):
    code = main(["draw", "--edition", "2026.Q4",
                 "--workdir", str(tmp_path / "work")])
    assert code == 2
