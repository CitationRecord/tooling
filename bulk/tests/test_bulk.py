"""Hermetic tests for the bulk loader. No network, no downloads.

    py -m pytest bulk/tests -q
"""

from __future__ import annotations

import bz2
import json

import pytest

from bulk import BUCKET_URL, DEFAULT_GENERATION, USER_AGENT
from bulk.catalog import BY_KEY, CATALOG, EXCLUDED, select
from bulk.config import REPO_ROOT, UnsafeDestination, check_destination, default_directory
from bulk.download import (
    IntegrityError,
    Manifest,
    FileRecord,
    iso_utc,
    load_manifest,
    save_manifest,
    sha256_file,
    verify,
)
from bulk.read import count_rows, open_rows, read_dicts

# A COPY-shaped CSV: header, ESCAPE '\', embedded comma, quote and newline.
CSV_BODY = (
    'id,volume,reporter,page,cluster_id\n'
    '1,576,"U.S.",644,2812209\n'
    '2,678,"F. Supp. 3d",1369,99\n'
    '3,,"Cal. App. 5th",1286,100\n'
    '4,347,"U.S. App. D.C.",262,101\n'
    '5,12,"Say \\"what\\", he said",5,102\n'
    '6,13,"two\nlines",6,103\n'
)


def write_bz2(path, body: str):
    with bz2.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write(body)
    return path


# --------------------------------------------------------------------------
# destination safety


def test_bulk_data_may_not_land_in_the_repository():
    with pytest.raises(UnsafeDestination):
        check_destination(REPO_ROOT / "bulk-data")


def test_bulk_data_may_not_land_in_the_claim_archive():
    with pytest.raises(UnsafeDestination):
        check_destination(REPO_ROOT.parent / "claim-archive" / "2026-06-30")


def test_the_repository_itself_is_refused():
    with pytest.raises(UnsafeDestination):
        check_destination(REPO_ROOT)


def test_the_default_directory_is_outside_both(tmp_path):
    directory = default_directory("2026-06-30")
    assert REPO_ROOT not in directory.parents
    assert directory.name == "2026-06-30"
    assert check_destination(directory) == directory.resolve()


def test_an_ordinary_directory_is_allowed(tmp_path):
    assert check_destination(tmp_path / "bulk") == (tmp_path / "bulk").resolve()


# --------------------------------------------------------------------------
# catalog


def test_courts_import_before_everything_that_depends_on_them():
    order = [f.key for f in select()]
    assert order.index("courts") < order.index("opinion-clusters")
    assert order.index("courts") < order.index("citations")


def test_schema_and_load_script_come_first():
    """They settle the layout before any large file is fetched."""
    assert [f.key for f in select()][:2] == ["schema", "load-script"]


def test_the_opinions_file_is_excluded_with_a_reason():
    assert "opinions" not in BY_KEY
    assert "51 GiB" in EXCLUDED["opinions"]


def test_object_keys_carry_the_generation():
    citations = BY_KEY["citations"]
    assert citations.object_key("2026-06-30") == "bulk-data/citations-2026-06-30.csv.bz2"
    assert BY_KEY["schema"].object_key("2026-06-30") == "bulk-data/schema-2026-06-30.sql"


def test_selecting_an_unknown_file_is_an_error():
    with pytest.raises(KeyError):
        select(["opinions"])


def test_every_catalog_entry_states_its_purpose():
    for bulk_file in CATALOG:
        assert bulk_file.purpose


def test_identity_is_truthful_and_unauthenticated():
    assert "CitationRecordBulk" in USER_AGENT
    assert "citationrecord.org" in USER_AGENT
    assert BUCKET_URL.startswith("https://")


def test_no_token_flag_exists():
    """The bucket is public; a credential flag would be misleading."""
    from bulk import cli

    rendered = cli.build_parser().format_help()
    for banned in ("--token", "--api-key", "--aws", "--secret"):
        assert banned not in rendered


# --------------------------------------------------------------------------
# manifest and integrity


def test_manifest_round_trips(tmp_path):
    manifest = Manifest(generation="2026-06-30", directory=str(tmp_path))
    manifest.record(FileRecord(
        key="citations", object_key="bulk-data/citations-2026-06-30.csv.bz2",
        path="citations-2026-06-30.csv.bz2", bytes=127359644,
        sha256="a" * 64, etag="b" * 32, etag_verified=True,
        last_modified="2026-06-30T00:00:00.000Z", downloaded_at_utc=iso_utc(),
    ))
    path = tmp_path / "manifest.json"
    save_manifest(manifest, path)

    reloaded = load_manifest(path)
    assert reloaded.generation == "2026-06-30"
    assert reloaded.files["citations"]["bytes"] == 127359644
    assert reloaded.total_bytes == 127359644


def test_manifest_states_that_the_drop_is_a_snapshot(tmp_path):
    """A census result must be traceable to one generation."""
    path = tmp_path / "manifest.json"
    save_manifest(Manifest(generation="2026-06-30", directory=str(tmp_path)), path)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["generation"] == "2026-06-30"
    assert "snapshot" in body["generation_note"].lower()
    assert "must state this generation" in body["generation_note"]


def test_a_truncated_manifest_reads_as_absent(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_manifest(path) is None


def test_verify_catches_a_size_change(tmp_path):
    target = tmp_path / "data.csv.bz2"
    target.write_bytes(b"hello")
    record = {"bytes": 99, "sha256": sha256_file(target)}
    assert any("size" in p for p in verify(target, record))


def test_verify_catches_a_content_change(tmp_path):
    target = tmp_path / "data.csv.bz2"
    target.write_bytes(b"hello")
    record = {"bytes": 5, "sha256": "0" * 64}
    assert any("sha256" in p for p in verify(target, record))


def test_verify_reports_a_missing_file(tmp_path):
    assert verify(tmp_path / "absent.bz2", {"bytes": 1, "sha256": "x"}) == [
        "missing: absent.bz2"
    ]


def test_verify_passes_an_intact_file(tmp_path):
    target = tmp_path / "data.csv.bz2"
    target.write_bytes(b"hello")
    assert verify(target, {"bytes": 5, "sha256": sha256_file(target)}) == []


# --------------------------------------------------------------------------
# reading COPY-shaped CSV


def test_reads_the_postgres_copy_dialect(tmp_path):
    """ESCAPE '\\' backslash-escapes quotes rather than doubling them."""
    path = write_bz2(tmp_path / "c.csv.bz2", CSV_BODY)
    rows = list(read_dicts(path, ["id", "reporter", "volume"]))
    assert len(rows) == 6
    assert rows[4]["reporter"] == 'Say "what", he said'


def test_embedded_newlines_do_not_split_a_row(tmp_path):
    """Counting newlines would overcount; a real CSV reader must be used."""
    path = write_bz2(tmp_path / "c.csv.bz2", CSV_BODY)
    _, rows = count_rows(path)
    assert rows == 6
    # 8 newlines: header, 6 row terminators, and one inside a quoted field.
    assert CSV_BODY.count("\n") == 8


def test_header_is_not_counted_as_a_row(tmp_path):
    path = write_bz2(tmp_path / "c.csv.bz2", CSV_BODY)
    header, rows = count_rows(path)
    assert header == ["id", "volume", "reporter", "page", "cluster_id"]
    assert rows == 6


def test_an_empty_volume_stays_empty_not_zero(tmp_path):
    """volume is a text column; a blank must not become 0 and merge volumes."""
    path = write_bz2(tmp_path / "c.csv.bz2", CSV_BODY)
    rows = list(read_dicts(path, ["reporter", "volume"]))
    blank = [r for r in rows if r["reporter"] == "Cal. App. 5th"][0]
    assert blank["volume"] == ""


def test_a_prefix_reporter_stays_distinct(tmp_path):
    """The bug the resolver fix addressed: U.S. must not absorb U.S. App. D.C."""
    path = write_bz2(tmp_path / "c.csv.bz2", CSV_BODY)
    reporters = {r["reporter"] for r in read_dicts(path, ["reporter"])}
    assert "U.S." in reporters
    assert "U.S. App. D.C." in reporters


def test_asking_for_a_missing_column_is_an_error(tmp_path):
    path = write_bz2(tmp_path / "c.csv.bz2", CSV_BODY)
    with pytest.raises(KeyError):
        list(read_dicts(path, ["no_such_column"]))


def test_plain_csv_reads_without_compression(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text(CSV_BODY, encoding="utf-8", newline="")
    with open_rows(path) as (header, reader):
        assert header[0] == "id"
        assert len(list(reader)) == 6


def test_an_empty_file_yields_no_rows(tmp_path):
    path = write_bz2(tmp_path / "empty.csv.bz2", "")
    header, rows = count_rows(path)
    assert header == [] and rows == 0


def test_default_generation_is_a_real_drop_date():
    assert DEFAULT_GENERATION == "2026-06-30"
