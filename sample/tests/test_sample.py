"""Hermetic tests for query selection. No network, no bulk data.

    py -m pytest sample/tests -q
"""

from __future__ import annotations

import json

import pytest

from register.config import UnsafeLocation
from sample import DRAW_KEY, METADATA_KINDS
from sample.cli import METADATA_PLAN, _metadata_check, main
from sample.config import workdir
from sample.draw import Draw, draw_key, new_seed, ordered, select
from sample.exclude import ExclusionList, load, load_all, normalise
from sample.negate import (
    RULE_PROVENANCE,
    has_stray_marker,
    negate,
    negator_governs_a_condition,
    opens_unbound,
    reject_reason,
    strip_prefix,
)
from sample.queryset import (
    ACCEPTABLE_RESPONSES,
    CATEGORY_SELECTION,
    metadata_query,
)


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


def test_a_second_sentence_is_rejected():
    """A semicolon is a sentence boundary. This is the guard that always worked.

    Renamed from "second clause" because that is what it was never checking:
    a comma and a coordinator open a second clause inside one sentence, and
    that shape passed this rule until the pilot found it.
    """
    text = ("holding that the rule does not apply here; the parties agreed "
            "otherwise in writing before the dispute arose")
    assert reject_reason(text) == "more than one sentence"


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


def test_an_inference_is_rejected_because_deleting_the_negator_breaks_it():
    """Deletion-only is necessary and not sufficient.

    The conclusion follows from the negated element, so removing the negation
    does not invert the claim, it breaks it. What comes out is incoherent
    rather than contrary, and a system cannot sensibly answer it.
    """
    text = ("holding that statements made as a union representative are not "
            "part of official police duties and thus are afforded First "
            "Amendment protection")
    assert reject_reason(text) == \
        "carries an inference; deleting the negator breaks it"
    assert negate(text) is None


def test_every_inferential_connective_disqualifies():
    stem = ("holding that the rule does not apply to the parties here %s it "
            "governs only later agreements")
    for word in ("thus", "therefore", "accordingly", "hence", "consequently",
                 "and so"):
        assert reject_reason(stem % word) is not None


def test_an_unbound_pronoun_is_rejected_because_it_does_not_stand_alone():
    """In the parenthetical "their" refers to the surrounding opinion. Lifted
    into a query it refers to nothing."""
    text = ("holding that their different procedural requirements do not "
            "render FLSA and state wage law class actions incompatible")
    assert reject_reason(text) == \
        "opens with an unbound pronoun; does not stand alone"
    assert negate(text) is None


def test_a_pronoun_later_in_the_clause_is_not_a_rejection():
    """Only the opener leaves the subject unstated."""
    text = ("holding that the statute does not reach a contractor where their "
            "work was performed wholly outside the state")
    assert not opens_unbound(strip_prefix(text))


def test_the_three_negations_that_survived_review_still_survive():
    """Kept from a real draw, so a later loosening of these rules is visible."""
    for text in (
        "holding that the “needs of the child” are not determined by "
        "the parents’ ability to pay or the lifestyle of the family",
        "holding that a statutory requirement of actual or constructive notice "
        "was not unconstitutionally vague",
        "holding that statutory provision governing review of single agency "
        "actions does not apply to challenge to a practice or procedure "
        "employed in making decisions generally",
    ):
        assert reject_reason(text) is None
        assert negate(text) is not None


def test_a_negator_inside_a_condition_is_rejected():
    """Deleting it flips the circumstance, not the claim. The source case held
    the first and does not hold the opposite of the second."""
    text = ("holding that ERISA applied when the employer could not carry out "
            "its obligations with an unthinking, one-time application")
    assert reject_reason(text) == "negator governs a condition, not the claim"
    assert negator_governs_a_condition(text)


def test_a_comma_set_off_subordinator_is_an_aside_not_a_clause():
    """The heuristic the docstring names. Without it this good item is lost."""
    text = ("holding that a defendant's true, if misleading, testimony cannot "
            "support a conviction under the federal perjury statute")
    assert not negator_governs_a_condition(text)
    assert reject_reason(text) is None


def test_the_rules_declare_themselves_empirical_and_not_exhaustive():
    assert RULE_PROVENANCE["derivation"] == "empirical"
    assert RULE_PROVENANCE["exhaustive"] is False
    assert "incomplete rather than" in RULE_PROVENANCE["implication"]
    assert any("heuristic" in h for h in RULE_PROVENANCE["heuristics"])


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


def test_a_malformed_case_name_is_rejected_not_repaired():
    """A scorer reading "State v. . Starnes" wonders whether the query or the
    corpus is broken. The corpus is the ground truth, so the candidate goes."""
    check = _metadata_check([])
    base = {"cluster_id": "1", "year": "1940", "reporter": "S.E.2d",
            "volume": "11", "page": "553", "citation_shared": False}
    for name in ("State v. . Starnes", "J.S. v.", "In re HH..", "Doe,, v. Roe"):
        assert check(dict(base, case_name=name)) ==             "punctuation damage in the case name"


def test_ordinary_case_names_survive_the_punctuation_check():
    check = _metadata_check([])
    base = {"cluster_id": "1", "year": "1940", "reporter": "S.E.2d",
            "volume": "11", "page": "553", "citation_shared": False}
    for name in ("Okke v. Okke (In re Okke)", "Sears, Roebuck & Co. v. Blade",
                 "United States v. Pfirsch", "In re the Petition of Doheny"):
        assert check(dict(base, case_name=name)) is None


def test_a_shared_citation_is_rejected_at_draw_time():
    """Six cases matched one drawn citation and three matched another. The
    bulk table gives no sign of it, so the pool counts and the draw records."""
    check = _metadata_check([])
    shared = {"cluster_id": "1", "year": "2011", "reporter": "A.3d",
              "volume": "32", "page": "836", "case_name": "Grese v. Grese",
              "citation_shared": True}
    assert check(shared) == "citation matches more than one case"
    assert check(dict(shared, citation_shared=False)) is None


def test_the_two_categories_declare_their_opposite_principles():
    assert CATEGORY_SELECTION["metadata"]["prefers"] == "obscurity"
    assert "prominence" in CATEGORY_SELECTION["negated-parenthetical"]["prefers"]
    assert "select against each other" in CATEGORY_SELECTION["asymmetry"]
    assert "not a defect" in CATEGORY_SELECTION["asymmetry"]


def test_the_three_branch_response_is_recorded_with_its_source():
    assert len(ACCEPTABLE_RESPONSES["branches"]) == 3
    assert "Magesh" in ACCEPTABLE_RESPONSES["source"]
    assert "supersedes" in ACCEPTABLE_RESPONSES["branches"][2]


def test_the_metadata_plan_is_two_authorship_one_year_one_citation():
    assert dict(METADATA_PLAN) == {"author": 2, "year": 1, "citation": 1}
    assert sum(n for _, n in METADATA_PLAN) == 4
    assert set(dict(METADATA_PLAN)) <= set(METADATA_KINDS)


def test_the_citation_query_waits_for_the_court():
    """Without the court the case may not be identifiable, and the court only
    exists after resolve. A finished-looking text would be under-specified."""
    candidate = {"cluster_id": "7", "case_name": "Doe v. Roe",
                 "reporter": "F. Supp.", "volume": "300", "page": "1",
                 "year": "1997", "citation_count": 1}
    query = metadata_query("citation", candidate)
    assert query["text"] is None
    assert "{court}" in query["text_template"]


def test_the_year_and_author_queries_are_final_at_draw_time():
    candidate = {"cluster_id": "7", "case_name": "Doe v. Roe",
                 "reporter": "F. Supp.", "volume": "300", "page": "1",
                 "year": "1997", "citation_count": 1}
    for kind in ("year", "author"):
        query = metadata_query(kind, candidate)
        assert query["text"] and query["text_template"] is None


def test_a_recent_case_is_rejected_however_few_times_it_is_cited():
    """citation_count <= 2 conflates unremarkable with not-yet-cited."""
    check = _metadata_check([])
    recent = {"cluster_id": "1", "case_name": "Doe v. Roe", "year": "2025",
              "reporter": "P.3d", "volume": "565", "page": "754"}
    assert "too recent" in check(recent)
    older = dict(recent, year="2007")
    assert check(older) is None


def test_a_docket_entry_is_not_a_case_name():
    check = _metadata_check([])
    junk = {"cluster_id": "1", "year": "2019", "reporter": "P.3d",
            "volume": "565", "page": "754",
            "case_name": "In re: The Petition for the Coordination of Maui "
                         "Fire Cases. S.Ct. Order, filed 02/10/2025 [ada]."}
    assert check(junk) == "docket entry rather than a case name"


def test_an_over_long_name_is_a_caption_fragment():
    check = _metadata_check([])
    candidate = {"cluster_id": "1", "year": "2019", "reporter": "P.3d",
                 "volume": "1", "page": "1", "case_name": "A" * 120}
    assert check(candidate) == "case name longer than a case name"


def test_a_stray_footnote_marker_is_rejected_not_repaired():
    """The original parenthetical is the ground truth. Editing it would leave
    the record disagreeing with the corpus it claims to quote."""
    text = ("holding that the State does not have to satisfy the prong of the "
            "test to be entitled to a lesser-included offense 13 instruction")
    assert reject_reason(text) == "stray footnote marker in the text"
    assert has_stray_marker(text)


def test_a_number_with_a_word_explaining_it_is_kept():
    for text in ("holding that Section 1983 liability cannot be based upon a "
                 "theory of respondeat superior or vicarious liability",
                 "holding that the trustee is not an \"individual\" under "
                 "former section 362 of the Bankruptcy Code as then written"):
        assert not has_stray_marker(text)


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


# --------------------------------------------------------------------------
# pilot draws, and keeping them apart from an edition's draw


def test_a_pilot_artifact_refuses_registration():
    """register/ reads registrable: false and exits 4. runner/ reads pilot."""
    from sample import pilot

    document = pilot.decorate({"seed": "abc"}, {"by_category": {}, "sources": []},
                              "pilot-2026.09")
    assert document["registrable"] is False
    assert document["pilot"] is True
    assert document["registrable_reason"]
    assert "burned" in document["items_are_burned"]


def test_prior_identifiers_are_read_without_the_seed(tmp_path):
    """Only the identifiers. Not the seed, the queries or the ground truth."""
    from sample import pilot

    edition = tmp_path / "edition.json"
    edition.write_text(json.dumps({
        "edition": "2026.Q4",
        "seed": "the-real-secret-seed",
        "queries": [
            {"category": "metadata", "subject": {"cluster_id": "111"},
             "text": "secret query", "ground_truth": {"answer": "secret"}},
            {"category": "negated-parenthetical",
             "subject": {"parenthetical_id": "222"}},
        ],
    }), encoding="utf-8")

    taken = pilot.drawn_identifiers([edition])
    assert taken["by_category"]["metadata"] == ["111"]
    assert taken["by_category"]["negated-parenthetical"] == ["222"]

    blob = json.dumps(taken)
    assert "the-real-secret-seed" not in blob
    assert "secret query" not in blob


def test_the_seed_fingerprint_proves_two_draws_differ(tmp_path):
    """A reader with both artifacts can check it; neither discloses the other."""
    from sample import pilot

    edition = tmp_path / "edition.json"
    edition.write_text(json.dumps({"edition": "2026.Q4", "seed": "aaa",
                                   "queries": []}), encoding="utf-8")
    taken = pilot.drawn_identifiers([edition])
    document = pilot.decorate({"seed": "bbb"}, taken, "pilot")

    assert document["seed_sha256"] == pilot.seed_fingerprint("bbb")
    recorded = document["distinct_from"]["sources"][0]["seed_sha256"]
    assert recorded == pilot.seed_fingerprint("aaa")
    assert recorded != document["seed_sha256"]
    assert "aaa" not in json.dumps(document)


def test_an_excluded_candidate_is_rejected_by_rule():
    from sample import pilot

    taken = {"by_category": {"metadata": {"111"}}, "sources": []}
    check = pilot.check(taken, "metadata")
    assert check("111") == pilot.EXCLUSION_RULE
    assert check("999") is None


def test_the_exclusion_is_a_rule_not_a_reviewer_decision():
    """It must not read as somebody's judgment in the artifact."""
    from sample import pilot

    taken = {"by_category": {"metadata": {"111"}}, "sources": []}
    reason = pilot.check(taken, "metadata")("111")
    assert isinstance(reason, str), "a tuple would record this as a reviewer"


# --------------------------------------------------------------------------
# the sixth failure shape: a comma and a coordinator


#: The exact parenthetical the 2026.09 pilot drew as neg-10314042, from the
#: CourtListener corpus. Pinned verbatim rather than paraphrased: a later
#: loosening of the clause guard must fail against the real text that broke it,
#: not against a tidied version of it.
GABELLI = (
    "holding that the “discovery rule” does not apply to civil "
    "penalty enforcement actions, and the statute of limitations starts "
    "running when the fraud occurs."
)


def test_the_gabelli_parenthetical_is_rejected():
    """The item that reached four models and could not have a right answer.

    Deleting "not" inverted the first clause and left the second, producing a
    query that contradicted itself: if the discovery rule *does* apply, the
    clock does not start when the fraud occurs. Three of four systems reported
    the contradiction instead of answering.

    This is a pinned regression. If it ever returns None again, the guard has
    been loosened back to the state that produced an unanswerable item.
    """
    from sample.negate import negate, reject_reason

    reason = reject_reason(GABELLI)
    assert reason is not None, "the Gabelli parenthetical must never be drawn"
    assert "coordinator" in reason
    assert negate(GABELLI) is None


def test_the_old_guard_would_have_passed_it():
    """Why the fix was needed, asserted rather than described.

    The previous guard counted full stops and semicolons. The Gabelli text has
    exactly one full stop and no semicolon, so it passed. Keeping this as a
    test means the distinction between a sentence boundary and a clause
    boundary stays visible to whoever reads these next.
    """
    assert GABELLI.count(".") == 1
    assert ";" not in GABELLI


def test_a_coordinated_second_clause_is_rejected_generally():
    from sample.negate import reject_reason

    text = ("holding that the statute does not apply to municipal employers, "
            "and the claim accrues on the date of discharge")
    assert "coordinator" in (reject_reason(text) or "")


def test_a_single_clause_negation_still_passes():
    """The fix must not close the category it is protecting."""
    from sample.negate import negate

    text = ("holding that a change in the statute of limitations was not "
            "foreseeable by the parties to the agreement")
    result = negate(text)
    assert result is not None
    assert result.negated.startswith("a change in the statute of limitations was")
    assert "not" not in result.negated.split()


# --------------------------------------------------------------------------
# authorship: a question with no correct answer is not a question


def test_the_pilot_item_would_no_longer_be_drawn():
    """410 B.R. 170 -- the item that asked four models an unanswerable question.

    Three of the four correctly said a single-judge court produces no majority
    opinion. Our ground truth recorded an author, so a scorer applying it would
    have marked the three that were right wrong. Pinned so a later loosening
    fails here rather than readmitting the item.
    """
    from sample.cli import authorship_is_undefined

    reason = authorship_is_undefined({
        "cluster_id": "1542423", "reporter": "B.R.", "volume": "410",
        "page": "170",
        "case_name": "Reunion Industries, Inc. v. Steel Partners II, L.P.",
    })
    assert reason is not None
    assert "no majority opinion" in reason


def test_every_single_judge_reporter_is_rejected_for_authorship():
    from sample.cli import authorship_is_undefined
    from sample.config import SINGLE_JUDGE_REPORTERS

    for reporter in SINGLE_JUDGE_REPORTERS:
        assert authorship_is_undefined(
            {"reporter": reporter, "case_name": "Smith v. Jones"}), reporter


def test_an_appellate_reporter_is_still_eligible():
    """The rule must not close the category it is protecting."""
    from sample.cli import authorship_is_undefined

    assert authorship_is_undefined(
        {"reporter": "N.E.2d", "case_name": "People v. Brooks"}) is None


def test_per_curiam_and_orders_are_rejected_by_name():
    from sample.cli import authorship_is_undefined

    for name in ("State v. Smith (Per Curiam)",
                 "In re the Marriage of Haddad",
                 "Jones v. Board, on the court's own motion"):
        assert authorship_is_undefined({"reporter": "A.2d", "case_name": name}), name


def test_the_rule_applies_only_to_the_authorship_question():
    """A single-judge decision has a perfectly good year and citation."""
    from sample.cli import metadata_kinds_affected

    assert metadata_kinds_affected() == ("author",)


def test_resolve_refuses_a_single_judge_court():
    """The second half of the guard, where the court is actually known."""
    from sample.truth import authorship_is_undefined as refuse

    assert refuse("District Court, W.D. Pennsylvania", {})
    assert refuse("United States Bankruptcy Court", {})
    assert refuse("Supreme Court of Colorado", {}) is None


def test_resolve_refuses_a_per_curiam_opinion():
    from sample.truth import authorship_is_undefined as refuse

    assert refuse("Supreme Court of Colorado", {"per_curiam": True})
    assert refuse("Supreme Court of Colorado", {"per_curiam": False}) is None


def test_a_refused_authorship_leaves_the_item_incomplete_not_wrong():
    """An incomplete item blocks registration. A wrong one gets published."""
    from sample.truth import majority_author

    class FakeClient:
        def _request(self, method, endpoint, params=None):
            class R:
                @staticmethod
                def json():
                    return {"results": [{"id": 1, "type": "010combined",
                                         "author_str": "MCVERRY",
                                         "per_curiam": False}]}
            return R()

    found = majority_author(FakeClient(), 1542423,
                            court="District Court, W.D. Pennsylvania")
    assert found["author"] is None
    assert "single judge" in found["undefined"]


# --------------------------------------------------------------------------
# a data gap is not somebody's judgment


def test_a_rule_sourced_rejection_names_a_condition_not_a_person(tmp_path):
    from sample import reviewed

    path = tmp_path / "reviewed-x.json"
    entry = reviewed.add(path, "5669476", "metadata",
                         "CourtListener records no author for this opinion",
                         source="rule",
                         condition="author-absent-in-courtlistener")
    assert entry["source"] == "rule"
    assert entry["decided_by"] is None
    assert entry["condition"] == "author-absent-in-courtlistener"
    assert entry["discovered_at"] == "resolve"
    assert reviewed.attribution(entry) == "rule:author-absent-in-courtlistener"


def test_a_rule_sourced_rejection_refuses_a_person(tmp_path):
    """The confusion this field exists to prevent, prevented."""
    from sample import reviewed

    with pytest.raises(ValueError, match="not a person"):
        reviewed.add(tmp_path / "r.json", "1", "metadata", "gap",
                     decided_by="JB Wagoner", source="rule",
                     condition="author-absent-in-courtlistener")


def test_an_unknown_condition_is_refused(tmp_path):
    from sample import reviewed

    with pytest.raises(ValueError, match="unknown condition"):
        reviewed.add(tmp_path / "r.json", "1", "metadata", "gap",
                     source="rule", condition="autohr-absent")


def test_a_reviewer_decision_must_still_name_someone(tmp_path):
    from sample import reviewed

    with pytest.raises(ValueError, match="must name who"):
        reviewed.add(tmp_path / "r.json", "1", "metadata", "judgment call")


def test_the_draw_records_the_two_sources_differently(tmp_path):
    """Three attributions, and an artifact keeps them apart."""
    from sample import reviewed

    path = tmp_path / "r.json"
    reviewed.add(path, "111", "metadata", "needs a doctrinal frame",
                 decided_by="JB Wagoner")
    reviewed.add(path, "222", "metadata", "no author recorded",
                 source="rule", condition="author-absent-in-courtlistener")

    check = reviewed.check(reviewed.load(path), "metadata")
    assert check("111")[1] == "reviewer:JB Wagoner"
    assert check("222")[1] == "rule:author-absent-in-courtlistener"
    assert check("333") is None


def test_an_entry_written_before_sources_reads_as_a_reviewer_decision():
    """Backwards compatible: that is all there was when it was written."""
    from sample import reviewed

    legacy = {"candidate_id": "1", "category": "metadata", "reason": "x",
              "decided_by": "JB Wagoner"}
    assert reviewed.attribution(legacy) == "reviewer:JB Wagoner"


def test_every_condition_explains_why_it_is_not_a_draw_time_rule():
    """A condition that could be checked at draw time should be one."""
    from sample import reviewed

    for name, detail in reviewed.CONDITIONS.items():
        assert detail["why_not_at_draw"], name
        assert detail["discovered_at"] in ("resolve",), name


# --------------------------------------------------------------------------
# the citation question must be answered in the form it asks for


def test_the_answer_is_a_bluebook_citation_not_a_reporter_cite():
    """The defect: the question asked for one thing, the answer held another.

    A system returning the correct Bluebook citation would not have matched a
    ground truth holding only "513 B.R. 896".
    """
    from sample import bluebook

    built = bluebook.answer(
        case_name="Okke v. Okke (In re Okke)",
        reporter_citation="513 B.R. 896",
        court_abbreviation="Bankr. W.D. Mich.",
        year=2014)
    assert built["answer"] == \
        "Okke v. Okke (In re Okke), 513 B.R. 896 (Bankr. W.D. Mich. 2014)"
    assert built["answer"] != "513 B.R. 896"


def test_the_court_is_the_bluebook_abbreviation_not_the_corpus_string():
    from sample import bluebook

    built = bluebook.answer("Adams v. State", "394 So. 2d 540",
                            "Fla. Dist. Ct. App.", 1981)
    assert "Fla. Dist. Ct. App." in built["answer"]
    assert "District Court of Appeal of Florida" not in built["answer"]


def test_a_missing_component_refuses_rather_than_building_a_gap():
    """A citation assembled around a blank looks like an answer."""
    from sample import bluebook

    for kwargs in (
        {"case_name": "", "reporter_citation": "1 A.2d 2",
         "court_abbreviation": "Tex.", "year": 1990},
        {"case_name": "A v. B", "reporter_citation": "1 A.2d 2",
         "court_abbreviation": None, "year": 1990},
        {"case_name": "A v. B", "reporter_citation": "1 A.2d 2",
         "court_abbreviation": "Tex.", "year": None},
    ):
        with pytest.raises(bluebook.Incomplete):
            bluebook.answer(**kwargs)


def test_the_components_travel_beside_the_whole():
    """So a scorer can say which part of a parallel citation differs."""
    from sample import bluebook

    built = bluebook.answer("People v. Lodge", "403 N.W.2d 591",
                            "Mich. Ct. App.", 1987)
    parts = built["answer_components"]
    assert parts["reporter_citation"] == "403 N.W.2d 591"
    assert parts["court_abbreviation"] == "Mich. Ct. App."
    assert parts["year"] == 1987
    assert built["matching_note"]


def test_the_year_question_is_answered_in_the_form_it_asks_for():
    """Checked alongside the citation one. This pair already agrees.

    The question asks for a year and the recorded answer is a year, so there
    is nothing to reconcile. Pinned so the agreement is asserted rather than
    assumed the next time somebody edits the template.
    """
    from sample.queryset import metadata_query

    query = metadata_query("year", {
        "cluster_id": "1", "case_name": "Adams v. State", "volume": "394",
        "reporter": "So. 2d", "page": "540", "year": "1981",
        "citation_count": 0})
    assert query["text"].startswith("What year was")
    assert "decided?" in query["text"]
