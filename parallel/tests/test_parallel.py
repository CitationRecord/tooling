"""Hermetic tests for the parallel reporter count. No network, no bulk data.

    py -m pytest parallel/tests -q
"""

from __future__ import annotations

import bz2
import json

import pytest

from parallel import ARTIFACT_KIND, SCHEMA
from parallel.cli import CAVEATS, _slug, main
from parallel.pairs import count_pair

# A COPY-shaped citations CSV in the real column order, with the escape and
# embedded-newline cases the dialect has to survive.
CSV_BODY = (
    'id,volume,reporter,page,type,cluster_id,date_created,date_modified\n'
    # cluster 100: both reporters
    '1,1,"Cal. Rptr. 3d",11,1,100,2024-01-01,2024-01-01\n'
    '2,1,"Cal. App. 5th",21,1,100,2024-01-01,2024-01-01\n'
    # cluster 101: Cal. Rptr. 3d only, cited twice in that reporter
    '3,2,"Cal. Rptr. 3d",12,1,101,2024-01-01,2024-01-01\n'
    '4,3,"Cal. Rptr. 3d",13,2,101,2024-01-01,2024-01-01\n'
    # cluster 102: Cal. App. 5th only
    '5,4,"Cal. App. 5th",22,1,102,2024-01-01,2024-01-01\n'
    # cluster 103: neither reporter
    '6,5,"U.S.",31,1,103,2024-01-01,2024-01-01\n'
    # a matching reporter with no cluster at all
    '7,6,"Cal. Rptr. 3d",14,1,,2024-01-01,2024-01-01\n'
    # rows the CSV dialect must not mis-split
    '8,7,"Say \\"what\\", he said",5,1,104,2024-01-01,2024-01-01\n'
    '9,8,"two\nlines",6,1,105,2024-01-01,2024-01-01\n'
)


def write_bz2(path, body: str = CSV_BODY):
    with bz2.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write(body)
    return path


@pytest.fixture()
def citations(tmp_path):
    return write_bz2(tmp_path / "citations-2026-06-30.csv.bz2")


# --------------------------------------------------------------------------
# counting


def test_clusters_are_counted_once_however_many_citations_they_carry(citations):
    result = count_pair(citations, "Cal. Rptr. 3d", "Cal. App. 5th")
    # cluster 101 carries two Cal. Rptr. 3d citations and counts once.
    assert result.clusters_a_only == 1
    assert result.rows_a == 3


def test_a_cluster_carrying_both_is_not_counted_as_a_gap(citations):
    result = count_pair(citations, "Cal. Rptr. 3d", "Cal. App. 5th")
    assert result.clusters_both == 1
    assert result.clusters_b_only == 1
    assert result.clusters_either == 3


def test_totals_include_the_overlap(citations):
    result = count_pair(citations, "Cal. Rptr. 3d", "Cal. App. 5th")
    assert result.clusters_a == result.clusters_a_only + result.clusters_both
    assert result.clusters_b == result.clusters_b_only + result.clusters_both


def test_a_citation_without_a_cluster_is_set_aside_not_dropped(citations):
    result = count_pair(citations, "Cal. Rptr. 3d", "Cal. App. 5th")
    assert result.rows_without_cluster == 1
    # It is excluded from the cluster counts, since it names no case.
    assert result.clusters_a == 2


def test_every_row_is_scanned_including_unmatched_reporters(citations):
    result = count_pair(citations, "Cal. Rptr. 3d", "Cal. App. 5th")
    assert result.rows_scanned == 9


def test_the_pair_is_symmetric(citations):
    forward = count_pair(citations, "Cal. Rptr. 3d", "Cal. App. 5th")
    reverse = count_pair(citations, "Cal. App. 5th", "Cal. Rptr. 3d")
    assert forward.clusters_a_only == reverse.clusters_b_only
    assert forward.clusters_both == reverse.clusters_both


def test_citation_types_are_recorded(citations):
    result = count_pair(citations, "Cal. Rptr. 3d", "Cal. App. 5th")
    assert result.types_a == {"1": 2, "2": 1}


def test_a_reporter_is_matched_exactly_not_fuzzily(citations):
    result = count_pair(citations, "Cal. Rptr 3d", "Cal. App. 5th")
    assert result.clusters_a == 0


def test_the_same_reporter_twice_is_refused(citations):
    with pytest.raises(ValueError):
        count_pair(citations, "Cal. Rptr. 3d", "Cal. Rptr. 3d")


def test_the_share_is_none_rather_than_a_division_by_zero(citations):
    result = count_pair(citations, "Not A Reporter", "Cal. App. 5th")
    assert result.a_only_share_of_a is None


def test_the_share_is_of_the_reporters_own_clusters(citations):
    result = count_pair(citations, "Cal. Rptr. 3d", "Cal. App. 5th")
    assert result.a_only_share_of_a == pytest.approx(0.5)


# --------------------------------------------------------------------------
# the artifact


def test_the_run_writes_an_artifact_that_names_its_source(tmp_path, citations):
    out = tmp_path / "artifact.json"
    code = main(["pair", "--reporter", "Cal. Rptr. 3d", "--against",
                 "Cal. App. 5th", "--dir", str(citations.parent),
                 "--out", str(out), "--quiet"])
    assert code == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["schema"] == SCHEMA
    assert document["artifact_kind"] == ARTIFACT_KIND
    assert document["source"]["file"] == citations.name
    assert document["clusters"]["a_only"] == 1
    assert document["clusters"]["both"] == 1


def test_the_artifact_says_a_missing_citation_is_not_a_missing_case(tmp_path, citations):
    out = tmp_path / "artifact.json"
    main(["pair", "--reporter", "Cal. Rptr. 3d", "--against", "Cal. App. 5th",
          "--dir", str(citations.parent), "--out", str(out), "--quiet"])
    caveats = " ".join(json.loads(out.read_text(encoding="utf-8"))["caveats"])
    assert "not an absent case" in caveats
    assert "is not a coverage gap" in caveats
    assert len(CAVEATS) >= 5


def test_a_misspelled_reporter_fails_rather_than_reporting_zero(tmp_path, citations):
    out = tmp_path / "artifact.json"
    code = main(["pair", "--reporter", "Cal. Rptr 3d", "--against",
                 "Cal. App. 5th", "--dir", str(citations.parent),
                 "--out", str(out), "--quiet"])
    assert code == 1
    assert not out.exists()


def test_a_missing_citations_file_is_reported_not_guessed(tmp_path):
    code = main(["pair", "--reporter", "Cal. Rptr. 3d", "--against",
                 "Cal. App. 5th", "--dir", str(tmp_path), "--quiet"])
    assert code == 2


def test_the_artifact_records_provenance_including_the_tooling_commit(tmp_path, citations):
    out = tmp_path / "artifact.json"
    main(["pair", "--reporter", "Cal. Rptr. 3d", "--against", "Cal. App. 5th",
          "--dir", str(citations.parent), "--out", str(out), "--quiet"])
    provenance = json.loads(out.read_text(encoding="utf-8"))["provenance"]
    assert "tooling" in provenance
    assert "parallel_version" in provenance


def test_slugs_are_filename_safe():
    assert _slug("Cal. Rptr. 3d") == "cal-rptr-3d"
    assert _slug("U.S. App. D.C.") == "u-s-app-d-c"
