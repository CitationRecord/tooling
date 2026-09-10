"""Hermetic tests for pre-registration. No network, no real journal.

    py -m pytest register/tests -q
"""

from __future__ import annotations

import json

import pytest

from register import CANONICAL_FORM, KINDS, SCHEMA
from register.cli import main
from register.config import UnsafeLocation, check_unpublished
from register.digest import canonical_json, digest_bytes, digest_file
from register.journal import (
    append,
    build_record,
    last_line_hash,
    read_records,
    verify_chain,
)

QUERIES = {"edition": "2026.Q4", "queries": [{"id": "a1", "text": "who wrote it"}]}


def write_json(path, payload=QUERIES, indent=2):
    path.write_text(json.dumps(payload, indent=indent), encoding="utf-8")
    return path


@pytest.fixture()
def private(tmp_path):
    """A stand-in for the private artifact repository."""
    root = tmp_path / "queries"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture()
def artifact(private):
    return write_json(private / "edition-2026Q4.json")


@pytest.fixture()
def journal(tmp_path):
    return tmp_path / "registrations.jsonl"


def run(args, journal, artifact=None, extra=()):
    argv = list(args) + ["--journal", str(journal)]
    if artifact is not None:
        argv += ["--artifact", str(artifact)]
    return main(argv + list(extra))


def add(journal, artifact, private, extra=()):
    return run(["add", "--kind", "query-set", "--edition", "2026.Q4",
                "--private-root", str(private)], journal, artifact, extra)


# --------------------------------------------------------------------------
# the destination guard
#
# This is the part that must not be got wrong. An unpublished query set in a
# public repository is published, whatever anyone intended.


def test_a_public_repo_root_is_refused(tmp_path):
    public = tmp_path / "site"
    public.mkdir()
    target = public / "queries.json"
    with pytest.raises(UnsafeLocation):
        check_unpublished(target, private_root=tmp_path / "queries",
                          forbidden_roots=(public,))


def test_a_subdirectory_of_a_public_repo_is_refused(tmp_path):
    public = tmp_path / "tooling"
    (public / "deep" / "nested").mkdir(parents=True)
    with pytest.raises(UnsafeLocation):
        check_unpublished(public / "deep" / "nested" / "q.json",
                          private_root=tmp_path / "queries",
                          forbidden_roots=(public,))


def test_an_unnamed_git_repository_is_refused(tmp_path):
    """The named list cannot enumerate every repository on the machine."""
    stranger = tmp_path / "somebody-elses-repo"
    (stranger / ".git").mkdir(parents=True)
    with pytest.raises(UnsafeLocation):
        check_unpublished(stranger / "q.json",
                          private_root=tmp_path / "queries",
                          forbidden_roots=())


def test_the_private_repository_is_allowed(private):
    target = private / "q.json"
    assert check_unpublished(target, private_root=private,
                             forbidden_roots=()) == target.resolve()


def test_a_path_in_no_repository_at_all_is_allowed(tmp_path):
    loose = tmp_path / "loose" / "q.json"
    loose.parent.mkdir()
    assert check_unpublished(loose, private_root=tmp_path / "queries",
                             forbidden_roots=()) == loose.resolve()


def test_the_guard_stops_the_command_not_just_the_function(tmp_path, journal):
    """The refusal has to reach the CLI, or it protects only unit tests."""
    stranger = tmp_path / "some-other-repo"
    (stranger / ".git").mkdir(parents=True)
    leak = write_json(stranger / "queries.json")
    code = main(["add", "--artifact", str(leak), "--kind", "query-set",
                 "--edition", "2026.Q4", "--journal", str(journal),
                 "--private-root", str(tmp_path / "queries")])
    assert code == 3
    assert not journal.exists()


def test_this_repository_is_refused_by_the_real_configuration(tmp_path, journal):
    """Not a synthetic root: the tooling checkout these tests run inside."""
    from register.config import REPO_ROOT

    with pytest.raises(UnsafeLocation):
        check_unpublished(REPO_ROOT / "register" / "leaked-queries.json")


def test_the_real_forbidden_list_names_the_public_repositories():
    from register.config import FORBIDDEN_ROOTS

    names = {p.name for p in FORBIDDEN_ROOTS}
    assert {"tooling", "site", "citation-resolutions", "claim-archive"} <= names


# --------------------------------------------------------------------------
# hashing


def test_canonical_form_ignores_formatting(tmp_path):
    a = digest_file(write_json(tmp_path / "a.json", indent=2))
    b = digest_file(write_json(tmp_path / "b.json", indent=None))
    assert a.sha256_bytes != b.sha256_bytes
    assert a.sha256_canonical == b.sha256_canonical


def test_canonical_form_ignores_key_order():
    one = digest_bytes(b'{"a":1,"b":2}')
    other = digest_bytes(b'{"b":2,"a":1}')
    assert one.sha256_canonical == other.sha256_canonical


def test_canonical_form_does_not_ignore_the_data():
    one = digest_bytes(b'{"a":1}')
    other = digest_bytes(b'{"a":2}')
    assert one.sha256_canonical != other.sha256_canonical


def test_a_trailing_newline_does_not_change_the_control():
    assert (digest_bytes(b'{"a":1}').sha256_canonical
            == digest_bytes(b'{"a":1}\n').sha256_canonical)


def test_non_json_falls_back_to_bytes_and_says_so():
    digest = digest_bytes(b"a frozen prompt protocol, in plain text")
    assert digest.sha256_canonical is None
    assert digest.controlling_kind == "bytes"
    assert digest.controlling == digest.sha256_bytes


def test_the_canonical_form_is_named_so_it_can_be_recomputed():
    assert digest_bytes(b'{"a":1}').canonical_form == CANONICAL_FORM
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


# --------------------------------------------------------------------------
# the journal


def test_registering_writes_a_record(journal, artifact, private):
    assert add(journal, artifact, private) == 0
    records = read_records(journal)
    assert len(records) == 1
    assert records[0]["schema"] == SCHEMA
    assert records[0]["kind"] == "query-set"
    assert records[0]["controls"] == "canonical"


def test_the_full_path_is_not_recorded(journal, artifact, private):
    add(journal, artifact, private)
    line = journal.read_text(encoding="utf-8")
    assert artifact.name in line
    assert str(artifact.parent) not in line


def test_the_chain_links_each_record_to_the_last(journal, artifact, private):
    add(journal, artifact, private)
    second = write_json(artifact.parent / "second.json", {"edition": "2027.Q1"})
    add(journal, second, private)
    records = read_records(journal)
    assert records[0]["prev_hash"] is None
    assert records[1]["prev_hash"] is not None
    ok, index, _ = verify_chain(journal)
    assert ok and index is None


def test_an_edited_record_breaks_the_chain(journal, artifact, private):
    add(journal, artifact, private)
    second = write_json(artifact.parent / "second.json", {"edition": "2027.Q1"})
    add(journal, second, private)
    lines = journal.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["edition"] = "2099.Q9"
    lines[0] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, index, _ = verify_chain(journal)
    assert not ok and index == 1


def test_a_removed_record_breaks_the_chain(journal, artifact, private):
    add(journal, artifact, private)
    second = write_json(artifact.parent / "second.json", {"edition": "2027.Q1"})
    add(journal, second, private)
    third = write_json(artifact.parent / "third.json", {"edition": "2027.Q2"})
    add(journal, third, private)
    lines = journal.read_text(encoding="utf-8").splitlines()
    journal.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    ok, index, _ = verify_chain(journal)
    assert not ok and index == 1


def test_registering_the_same_content_twice_is_refused(journal, artifact, private):
    assert add(journal, artifact, private) == 0
    assert add(journal, artifact, private) == 1
    assert len(read_records(journal)) == 1


def test_a_deliberate_second_registration_is_allowed(journal, artifact, private):
    add(journal, artifact, private)
    assert add(journal, artifact, private, extra=["--again"]) == 0
    assert len(read_records(journal)) == 2


def test_superseding_an_unknown_record_is_refused(journal, artifact, private):
    code = add(journal, artifact, private, extra=["--supersedes", "deadbeef"])
    assert code == 2
    assert not journal.exists()


# --------------------------------------------------------------------------
# verification


def test_verify_finds_a_registered_artifact(journal, artifact, private):
    add(journal, artifact, private)
    assert run(["verify"], journal, artifact) == 0


def test_verify_tolerates_reformatting(journal, artifact, private):
    add(journal, artifact, private)
    write_json(artifact, indent=None)
    assert run(["verify"], journal, artifact) == 0


def test_verify_rejects_changed_data(journal, artifact, private):
    add(journal, artifact, private)
    write_json(artifact, {"edition": "2026.Q4", "queries": [{"id": "a1",
                                                            "text": "changed"}]})
    assert run(["verify"], journal, artifact) == 1


def test_verify_rejects_an_unregistered_artifact(journal, artifact, private):
    assert run(["verify"], journal, artifact) == 1


# --------------------------------------------------------------------------
# surface


def test_kinds_are_closed(journal, artifact, private):
    with pytest.raises(SystemExit):
        main(["add", "--artifact", str(artifact), "--kind", "querry-set",
              "--edition", "2026.Q4", "--journal", str(journal)])


def test_query_set_and_ground_truth_are_both_registrable():
    assert "query-set" in KINDS
    assert "ground-truth" in KINDS


def test_listing_an_empty_journal_says_so(journal):
    assert run(["list"], journal) == 0


def test_chain_on_an_empty_journal_is_intact(journal):
    assert run(["chain"], journal) == 0
