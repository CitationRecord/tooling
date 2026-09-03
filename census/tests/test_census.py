"""Hermetic tests for the coverage scope probe. No network, no API token.

    py -m pytest census/tests -q
"""

from __future__ import annotations

import json

import pytest

from census import ARTIFACT_KIND
from census.frame import PROBE_FRAME, Reporter, build_plan, sample_volumes, strata
from census.probe import Probe, Stopped
from census.report import build_document, summarise, to_markdown
from census.store import ProbeState, Store, VolumeResult, iso_utc

from resolve.client import RateLimited

FAKE = Reporter(
    key="F. Test 3d", name="Test Reporter", category="test",
    first_volume=1, last_volume=300, range_source="fixture",
)


# --------------------------------------------------------------------------
# the probe must never be mistaken for the census


def test_every_output_is_labelled_a_probe():
    document = build_document(_state_with_results())
    assert document["artifact_kind"] == ARTIFACT_KIND == "scope_probe"
    assert document["not_publishable_as"] == "methodology section 4.2"
    assert "SCOPE PROBE" in document["limitations"][0]


def test_markdown_says_what_it_is_not_before_any_number():
    markdown = to_markdown(build_document(_state_with_results()))
    heading, banner = markdown.split("\n")[0], markdown.split("\n")[2]
    assert "scope probe" in heading.lower()
    assert "Not a census" in banner
    assert "4.2" in banner
    # The disclaimer precedes the table.
    assert markdown.index("Not a census") < markdown.index("| Reporter |")


def test_state_file_carries_the_warning(tmp_path):
    store = Store(tmp_path / "state.json")
    store.save(ProbeState())
    body = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert body["artifact_kind"] == "scope_probe"
    assert "not a census" in body["warning"].lower()


def test_limitations_state_the_required_three():
    document = build_document(_state_with_results())
    joined = " ".join(document["limitations"]).lower()
    assert "n=" in joined                    # sample size per reporter
    assert "proxy for coverage" in joined    # counts are a proxy
    assert "inferred from" in joined         # page range is inferred


def test_limitations_warn_that_zero_is_not_absence():
    """A zero count measures citation coverage, not case coverage: the case
    may be held under a parallel reporter."""
    joined = " ".join(build_document(_state_with_results())["limitations"])
    assert "parallel" in joined.lower()
    assert "Cal. Rptr. 3d" in joined


# --------------------------------------------------------------------------
# sampling frame


def test_strata_split_the_range_into_three():
    bands = strata(FAKE)
    assert [b.name for b in bands] == ["early", "middle", "recent"]
    assert bands[0].low == 1
    assert bands[-1].high == 300
    for earlier, later in zip(bands, bands[1:]):
        assert earlier.high < later.low


def test_sampling_is_deterministic():
    """A rerun must hit the same volumes or the probe is not reproducible."""
    assert sample_volumes(FAKE, 4) == sample_volumes(FAKE, 4)


def test_sampling_spans_every_stratum():
    volumes = sample_volumes(FAKE, 4)
    bands = strata(FAKE)
    hit = {b.name for b in bands for v in volumes if b.low <= v <= b.high}
    assert hit == {"early", "middle", "recent"}


def test_remainder_is_weighted_to_recent_volumes():
    volumes = sample_volumes(FAKE, 4)
    recent = strata(FAKE)[2]
    assert sum(1 for v in volumes if recent.low <= v <= recent.high) == 2


def test_sampled_volumes_are_unique_and_in_range():
    volumes = sample_volumes(FAKE, 4)
    assert len(set(volumes)) == len(volumes) == 4
    assert all(FAKE.first_volume <= v <= FAKE.last_volume for v in volumes)


def test_volume_ranges_are_declared_not_derived():
    """Reading the range from CourtListener would hide the gaps measured."""
    for reporter in PROBE_FRAME:
        assert reporter.range_source
        assert reporter.last_volume > reporter.first_volume


def test_probe_frame_covers_the_four_asked_for():
    assert {r.key for r in PROBE_FRAME} == {
        "U.S.", "F.3d", "F. Supp. 3d", "Cal. App. 5th"
    }


def test_plan_estimates_stay_inside_the_budget():
    plan = build_plan(PROBE_FRAME, per_reporter=4)
    best, worst = plan.estimated_requests()
    assert plan.total_volumes == 16
    assert worst < 80


# --------------------------------------------------------------------------
# resumability


class FakeClient:
    def __init__(self, holdings=None, fail_after=None):
        self.holdings = holdings or {}
        self.fail_after = fail_after
        self.volume_calls = 0
        self.presence_calls = 0
        self.calls = []

    def volume_holdings(self, reporter, volume, sample=20):
        self.volume_calls += 1
        if self.fail_after is not None and self.volume_calls > self.fail_after:
            raise RateLimited("rate limit reached")
        return self.holdings.get(
            (reporter, volume),
            {"count": 7, "pages": [10, 20], "pages_are_complete": True, "sampled": 2},
        )

    def reporter_has_opinions(self, reporter):
        self.presence_calls += 1
        return 20


def small_plan():
    return build_plan((FAKE,), per_reporter=4)


def test_a_rate_limit_stops_the_run_without_losing_work(tmp_path):
    store = Store(tmp_path / "state.json")
    client = FakeClient(fail_after=2)
    probe = Probe(client, store)
    plan = small_plan()
    state = probe.state_for(plan, {})

    with pytest.raises(Stopped):
        probe.run(plan, state)

    reloaded = store.load()
    assert len(reloaded.measured) == 2
    assert len(reloaded.remaining()) == 2


def test_a_rerun_continues_rather_than_restarting(tmp_path):
    store = Store(tmp_path / "state.json")
    plan = small_plan()

    first = FakeClient(fail_after=2)
    probe = Probe(first, store)
    with pytest.raises(Stopped):
        probe.run(plan, probe.state_for(plan, {}))

    second = FakeClient()
    resumed = Probe(second, store)
    state = resumed.run(plan, resumed.state_for(plan, {}))

    assert len(state.measured) == 4
    assert not state.remaining()
    # Only the two unmeasured volumes were fetched again.
    assert second.volume_calls == 2


def test_presence_is_checked_once_per_reporter(tmp_path):
    store = Store(tmp_path / "state.json")
    client = FakeClient()
    probe = Probe(client, store)
    plan = small_plan()
    probe.run(plan, probe.state_for(plan, {}))
    assert client.presence_calls == 1


def test_a_changed_plan_refuses_to_reuse_old_state(tmp_path):
    store = Store(tmp_path / "state.json")
    probe = Probe(FakeClient(), store)
    probe.run(small_plan(), probe.state_for(small_plan(), {}))

    wider = build_plan((FAKE,), per_reporter=6)
    with pytest.raises(Stopped):
        probe.state_for(wider, {})


def test_a_budget_stops_the_run_early(tmp_path):
    store = Store(tmp_path / "state.json")
    client = FakeClient()
    probe = Probe(client, store)
    plan = small_plan()
    state = probe.run(plan, probe.state_for(plan, {}), budget=2)
    assert len(state.measured) < plan.total_volumes
    assert state.remaining()


def test_state_survives_a_truncated_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    assert Store(path).load() is None


# --------------------------------------------------------------------------
# statistics


def _state_with_results() -> ProbeState:
    state = ProbeState(
        per_reporter=4,
        plan={"F. Test 3d": [10, 100, 200, 290]},
        frame=[FAKE.as_dict()],
        reporter_present={"F. Test 3d": 20},
    )
    for volume, count, low, high in (
        (10, 0, None, None),
        (100, 4, 120, 140),
        (200, 60, 1, 900),
        (290, 100, 5, 990),
    ):
        state.record(VolumeResult(
            reporter="F. Test 3d", volume=volume, stratum="early",
            opinion_count=count, page_low=low, page_high=high,
            pages_sampled=2 if low else 0, pages_are_complete=True,
            measured_at_utc=iso_utc(),
        ))
    return state


def test_statistics_are_arithmetic_over_the_samples():
    stats = summarise(_state_with_results())[0]
    assert stats.volumes_measured == 4
    assert stats.median_opinions == 32.0      # median of 0, 4, 60, 100
    assert stats.mean_opinions == 41.0
    assert stats.min_opinions == 0
    assert stats.max_opinions == 100


def test_zero_and_sparse_proportions():
    stats = summarise(_state_with_results())[0]
    assert stats.proportion_zero == 0.25       # one of four
    assert stats.proportion_under_50 == 0.5    # 0 and 4 are under 50


def test_errored_volumes_are_excluded_from_statistics():
    state = _state_with_results()
    state.record(VolumeResult(
        reporter="F. Test 3d", volume=290, stratum="recent",
        opinion_count=0, page_low=None, page_high=None, pages_sampled=0,
        pages_are_complete=False, measured_at_utc=iso_utc(), error="boom",
    ))
    stats = summarise(state)[0]
    assert stats.volumes_measured == 3
    assert stats.volumes_errored == 1


def test_a_reporter_with_nothing_measured_reports_no_statistics():
    state = ProbeState(plan={"F. Test 3d": [1, 2]}, frame=[FAKE.as_dict()])
    stats = summarise(state)[0]
    assert stats.volumes_measured == 0
    assert stats.median_opinions is None


def test_document_records_which_volumes_were_sampled():
    """Reproducibility: the exact volumes must be in the output."""
    document = build_document(_state_with_results())
    assert document["plan"] == {"F. Test 3d": [10, 100, 200, 290]}
    volumes = [v["volume"] for v in document["reporters"][0]["volumes"]]
    assert volumes == [10, 100, 200, 290]


def test_document_serialises():
    assert json.dumps(build_document(_state_with_results()))
