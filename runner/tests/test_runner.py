"""Hermetic tests for the runner. No network, no credentials, no real run.

    py -m pytest runner/tests -q

The ones to read first are the gate tests. They cover every cell of the table
that keeps a pilot out of an edition and an unregistered edition out of a model,
including the two cells that refuse in the direction nobody expects: a
registered set may not be run as a pilot, and a pilot set may not be run
without saying so.

After those, the write-once tests and the "a bad answer is still an answer"
tests, which are where a convenience would do the most damage.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from register.digest import sha256_text
from register.journal import append, build_record
from register.digest import digest_file

from runner import FIDELITY, OUTCOMES, SCHEMA
from runner import protocol as protocol_mod
from runner import run as run_mod
from runner import systems as systems_mod
from runner import transcript as transcript_mod
from runner.adapters import anthropic, google, openai, wire as wire_mod, xai
from runner.cli import main


# -- fakes ----------------------------------------------------------------


class FakeRequest:
    """What `requests` prepares. The body here is the body that goes out."""

    def __init__(self, method, url, headers, body):
        self.method, self.url, self.headers, self.body = method, url, headers, body


class FakeResponse:
    def __init__(self, status, body, headers=None, request=None):
        self.status_code = status
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.headers = headers or {"content-type": "application/json"}
        self.encoding = "utf-8"
        self.request = request


class FakeSession:
    """Returns a scripted sequence, and records what it was asked to send."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.sent.append({"method": method, "url": url,
                          "headers": dict(headers or {}), "body": data})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return FakeResponse(status, body,
                            request=FakeRequest(method, url, headers, data))

    def close(self):
        pass


ANSWER = json.dumps({
    "id": "msg_1",
    "model": "claude-opus-5",
    "content": [{"type": "text", "text": "See Smith v. Jones, 1 F. Supp. 2d 3."}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 20},
})


PROTOCOL = {
    "schema": "citationrecord.runner.protocol.v1",
    "protocol_version": "v1",
    "system_prompt": {"text": "When you rely on a case, give its citation."},
    "systems": {
        "claude": {"model": "claude-opus-5", "max_output_tokens": 999,
                   "sampling": {"requested": {},
                                "unsupported": ["temperature"]},
                   "extra": {"thinking": {"type": "adaptive"}}},
        "gpt-5": {"model": "gpt-5", "max_output_tokens": 999,
                  "sampling": {"requested": {}, "unsupported": ["temperature"]}},
        "gemini": {"model": "gemini-2.5-pro", "max_output_tokens": 999,
                   "sampling": {"requested": {"temperature": 0},
                                "unsupported": []}},
        "grok": {"model": "grok-4", "max_output_tokens": 999,
                 "sampling": {"requested": {"temperature": 0},
                              "unsupported": []}},
    },
}


QUERIES = [
    {"id": "meta-year-1", "category": "metadata", "metadata_kind": "year",
     "text": "What year was Smith v. Jones, 1 F. Supp. 2d 3, decided?"},
]


def queryset(**overrides) -> dict:
    document = {"edition": "pilot-2026.09", "schema": "x", "queries": QUERIES}
    document.update(overrides)
    return document


PILOT_SET = dict(
    pilot=True,
    registrable=False,
    registrable_reason="These items are burned and this is not Edition One.",
)


@pytest.fixture()
def protocol_dir(tmp_path):
    directory = tmp_path / "protocols"
    directory.mkdir()
    path = directory / "prompt-protocol-v1.json"
    path.write_text(json.dumps(PROTOCOL, indent=2), encoding="utf-8")
    return directory


@pytest.fixture()
def protocol(protocol_dir):
    return protocol_mod.load("v1", protocol_dir)


@pytest.fixture()
def journal(tmp_path):
    return tmp_path / "registrations.jsonl"


@pytest.fixture()
def runs(tmp_path):
    return tmp_path / "runs"


def register(journal, path, kind):
    """Put a real registration record in a journal, the way register/ does."""
    append(journal, build_record(kind=kind, edition="2026.Q4",
                                 filename=Path(path).name,
                                 digest=digest_file(path), prev_hash=None))


def write_set(tmp_path, document, name="set.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


# -- the gate, in both directions -----------------------------------------


def test_registered_set_runs_without_pilot():
    run_mod.gate(queryset(), registered=True, pilot_flag=False)


def test_registered_set_refuses_pilot():
    """A registered set is an edition's set. Running it as a pilot burns it."""
    with pytest.raises(run_mod.GateRefused, match="registered query set"):
        run_mod.gate(queryset(), registered=True, pilot_flag=True)


def test_unregistered_plain_set_refuses():
    """The ordering commitment, enforced rather than remembered."""
    with pytest.raises(run_mod.GateRefused, match="unregistered query set"):
        run_mod.gate(queryset(), registered=False, pilot_flag=False)


def test_unregistered_plain_set_refuses_pilot_too():
    with pytest.raises(run_mod.GateRefused, match="pilot: true"):
        run_mod.gate(queryset(), registered=False, pilot_flag=True)


def test_pilot_set_runs_with_pilot_flag():
    run_mod.gate(queryset(**PILOT_SET), registered=False, pilot_flag=True)


def test_pilot_set_refuses_without_the_flag():
    """A pilot must say so on the command line as well as in the file."""
    with pytest.raises(run_mod.GateRefused, match="without --pilot"):
        run_mod.gate(queryset(**PILOT_SET), registered=False, pilot_flag=False)


def test_pilot_flag_needs_both_declarations():
    """pilot: true alone is not enough; the set must also decline registration."""
    half = queryset(pilot=True)
    with pytest.raises(run_mod.GateRefused, match="registrable: false"):
        run_mod.gate(half, registered=False, pilot_flag=True)


def test_gate_reaches_the_command(tmp_path, protocol_dir, journal, runs):
    """The refusal is exit 4 at the command, not only in the function."""
    path = write_set(tmp_path, queryset(**PILOT_SET))
    code = main(["plan", "--queryset", str(path), "--dir", str(protocol_dir),
                 "--journal", str(journal)])
    assert code == 4


def test_pilot_plan_is_allowed(tmp_path, protocol_dir, journal, capsys):
    path = write_set(tmp_path, queryset(**PILOT_SET))
    code = main(["plan", "--queryset", str(path), "--dir", str(protocol_dir),
                 "--journal", str(journal), "--pilot"])
    assert code == 0
    out = capsys.readouterr().out
    assert "PILOT" in out
    assert "OUT OF SCOPE" in out


def test_run_refuses_an_unregistered_protocol(tmp_path, protocol_dir, journal,
                                              runs, monkeypatch):
    """Exit 5, and it is reached before anything is sent."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    path = write_set(tmp_path, queryset(**PILOT_SET))
    code = main(["run", "--queryset", str(path), "--dir", str(protocol_dir),
                 "--journal", str(journal), "--pilot", "--systems", "claude",
                 "--runs", str(runs)])
    assert code == 5
    assert not runs.exists()


# -- the protocol ---------------------------------------------------------


def test_protocol_hash_is_the_identity(protocol_dir):
    first = protocol_mod.load("v1", protocol_dir)
    body = json.loads((protocol_dir / "prompt-protocol-v1.json")
                      .read_text(encoding="utf-8"))
    body["system_prompt"]["text"] = "Cite cases."
    (protocol_dir / "prompt-protocol-v1.json").write_text(
        json.dumps(body, indent=2), encoding="utf-8")
    second = protocol_mod.load("v1", protocol_dir)
    assert first.digest.sha256_canonical != second.digest.sha256_canonical


def test_protocol_reformatting_does_not_change_the_control(protocol_dir):
    """The canonical hash ignores what does not change the data."""
    first = protocol_mod.load("v1", protocol_dir)
    path = protocol_dir / "prompt-protocol-v1.json"
    path.write_text(json.dumps(PROTOCOL, indent=8) + "\n\n", encoding="utf-8")
    second = protocol_mod.load("v1", protocol_dir)
    assert first.digest.sha256_canonical == second.digest.sha256_canonical
    assert first.digest.sha256_bytes != second.digest.sha256_bytes


def test_version_must_match_the_filename(protocol_dir):
    """What catches v1 edited in place, or v2 copied and not renamed."""
    body = dict(PROTOCOL, protocol_version="v2")
    (protocol_dir / "prompt-protocol-v1.json").write_text(
        json.dumps(body), encoding="utf-8")
    with pytest.raises(protocol_mod.ProtocolError, match="never edited"):
        protocol_mod.load("v1", protocol_dir)


def test_protocol_registration_is_found(protocol_dir, journal):
    protocol = protocol_mod.load("v1", protocol_dir)
    assert protocol_mod.registration(protocol, journal) is None
    register(journal, protocol.path, "prompt-protocol")
    assert protocol_mod.registration(protocol, journal) is not None


def test_a_system_with_no_block_is_refused(protocol):
    with pytest.raises(protocol_mod.ProtocolError, match="no section"):
        protocol.block("mistral")


# -- write-once -----------------------------------------------------------


def test_a_second_write_fails_rather_than_overwrites(tmp_path):
    path = tmp_path / "t.json"
    transcript_mod.write_once(path, "first", seal=False)
    with pytest.raises(transcript_mod.AlreadyWritten):
        transcript_mod.write_once(path, "second", seal=False)
    assert path.read_text(encoding="utf-8") == "first"


def test_sealed_transcripts_are_read_only(tmp_path):
    path = tmp_path / "sealed.json"
    transcript_mod.write_once(path, "fixed", seal=True)
    assert not (path.stat().st_mode & stat.S_IWRITE)
    os.chmod(path, stat.S_IWRITE)  # so the temp directory can be cleaned


def test_a_rerun_is_a_new_attempt_and_keeps_both(runs):
    store = transcript_mod.RunStore(runs, "r1", seal=False)
    assert store.next_attempt("q1", "claude") == 1
    transcript_mod.write_once(store.transcript_path("q1", "claude", 1), "{}",
                              seal=False)
    assert store.next_attempt("q1", "claude") == 2


def test_the_index_is_chained(runs):
    store = transcript_mod.RunStore(runs, "r1", seal=False)
    store.manifest({"queries": [], "systems": []})
    for index in range(3):
        store.record(_transcript(f"q{index}"))
    report = transcript_mod.verify(store.root)
    assert report["chain_ok"] and report["lines"] == 3
    assert not report["mismatched"] and not report["missing"]


def test_verification_catches_an_edited_transcript(runs):
    store = transcript_mod.RunStore(runs, "r1", seal=False)
    store.manifest({"queries": [], "systems": []})
    line = store.record(_transcript("q1"))
    path = store.root / transcript_mod.TRANSCRIPT_DIR / line["transcript"]
    path.write_text('{"edited": true}\n', encoding="utf-8")
    report = transcript_mod.verify(store.root)
    assert report["mismatched"] == [line["transcript"]]


def test_verification_catches_a_removed_index_line(runs):
    store = transcript_mod.RunStore(runs, "r1", seal=False)
    store.manifest({"queries": [], "systems": []})
    store.record(_transcript("q1"))
    store.record(_transcript("q2"))
    lines = store.index_path.read_text(encoding="utf-8").splitlines()
    store.index_path.write_text(lines[1] + "\n", encoding="utf-8")
    report = transcript_mod.verify(store.root)
    assert not report["chain_ok"]


def test_the_run_directory_refuses_a_repository(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(transcript_mod.check_no_repository.__globals__[
            "UnsafeLocation"]):
        transcript_mod.RunStore(tmp_path, "r1", seal=False)


def _transcript(query_id: str) -> dict:
    return {
        "schema": SCHEMA,
        "attempt": 1,
        "outcome": "response",
        "pilot": True,
        "query": {"id": query_id},
        "system": {"id": "claude", "published_scope": True},
    }


# -- a bad answer is still an answer --------------------------------------


@pytest.mark.parametrize("status,body", [
    (200, ""),                                   # empty
    (200, "not json at all"),                    # malformed
    (200, json.dumps({"content": []})),          # no text block
    (400, json.dumps({"error": {"message": "bad"}})),  # rejected
])
def test_odd_responses_are_stored_and_never_retried(status, body, protocol,
                                                    monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    session = FakeSession((status, body))
    step = run_mod.Step(query=QUERIES[0], system=systems_mod.by_id("claude"),
                        sequence=1)
    from runner.config import Config

    transcript = run_mod.converse(session, step, protocol, "r1", True,
                                  Config().resolved())
    assert len(session.sent) == 1, "an answer was retried"
    assert transcript["response"]["body"] == body
    assert transcript["response"]["http_status"] == status


def test_a_refusal_is_recorded_as_a_response(protocol, monkeypatch):
    """HTTP 200 with stop_reason refusal is an answer, not a failure."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    body = json.dumps({"model": "claude-opus-5", "content": [],
                       "stop_reason": "refusal",
                       "stop_details": {"type": "refusal", "category": "x"}})
    session = FakeSession((200, body))
    step = run_mod.Step(query=QUERIES[0], system=systems_mod.by_id("claude"),
                        sequence=1)
    from runner.config import Config

    transcript = run_mod.converse(session, step, protocol, "r1", True,
                                  Config().resolved())
    assert transcript["outcome"] == "response"
    assert transcript["response"]["stop_reason"] == "refusal"
    assert transcript["response"]["stop_detail"]["category"] == "x"


def test_a_transport_failure_retries_and_records_every_attempt(protocol,
                                                               monkeypatch):
    import requests

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    session = FakeSession(
        requests.ConnectionError("dropped"),
        requests.ConnectionError("dropped again"),
        (200, ANSWER),
    )
    step = run_mod.Step(query=QUERIES[0], system=systems_mod.by_id("claude"),
                        sequence=1)
    from runner.config import Config

    transcript = run_mod.converse(session, step, protocol, "r1", True,
                                  Config().resolved(), sleep=lambda s: None)
    assert transcript["outcome"] == "response"
    assert len(transcript["attempts_log"]) == 3
    assert transcript["attempts_log"][0]["error"].startswith("ConnectionError")


def test_the_response_is_stored_byte_for_byte(protocol, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    odd = '{"content":[{"type":"text","text":"  ragged\\n\\n  spacing  "}]}'
    session = FakeSession((200, odd))
    step = run_mod.Step(query=QUERIES[0], system=systems_mod.by_id("claude"),
                        sequence=1)
    from runner.config import Config

    transcript = run_mod.converse(session, step, protocol, "r1", True,
                                  Config().resolved())
    assert transcript["response"]["body"] == odd
    assert transcript["response"]["text"] == "  ragged\n\n  spacing  "
    assert transcript["response"]["sha256_bytes"] == sha256_text(odd)


# -- sampling -------------------------------------------------------------


def test_a_dropped_parameter_is_visible(protocol):
    """Asked for, not in the body: the difference is the finding."""
    block = {"model": "m", "max_output_tokens": 1,
             "sampling": {"requested": {"temperature": 0}, "unsupported": []}}
    record = run_mod.sampling_record(block, {"model": "m"})
    assert record["as_sent"]["temperature"] is None
    assert record["dropped"] == ["temperature"]


def test_a_nested_parameter_counts_as_sent(protocol):
    """Gemini puts temperature inside generationConfig; it still went."""
    block = {"model": "m", "max_output_tokens": 1,
             "sampling": {"requested": {"temperature": 0}, "unsupported": []}}
    record = run_mod.sampling_record(block, {"generationConfig": {"temperature": 0}})
    assert record["as_sent"]["temperature"] == 0
    assert record["dropped"] == []


def test_unsupported_is_recorded_not_sent(protocol):
    """Claude has no temperature. The record says so rather than inventing one."""
    block = protocol.block("claude")
    _, _, _, body = anthropic.build(block, "q", None, "key")
    record = run_mod.sampling_record(block, body)
    assert "temperature" not in body
    assert record["unsupported"] == ["temperature"]
    assert record["as_sent"] == {}


# -- raw model ------------------------------------------------------------


@pytest.mark.parametrize("module", [anthropic, openai, google, xai])
def test_no_adapter_declares_tools(module, protocol):
    system = {anthropic: "claude", openai: "gpt-5",
              google: "gemini", xai: "grok"}[module]
    _, _, _, body = module.build(protocol.block(system), "q", "sys", "key")
    assert "tools" not in body
    assert module.RETRIEVAL_OFF


def test_grok_sends_no_search_parameters(protocol):
    """xAI removed Live Search; the parameter now returns HTTP 410.

    Retrieval is off because no tools are declared, as for the other three.
    Sending search_parameters would fail the request rather than document
    anything, which `runner probe` found on a throwaway sentence.
    """
    _, _, _, body = xai.build(protocol.block("grok"), "q", None, "key")
    assert "search_parameters" not in body
    assert "tools" not in body


def test_a_protocol_edit_cannot_reintroduce_a_removed_parameter(protocol):
    """It is not a stricter setting. It is a rejected one."""
    block = dict(protocol.block("grok"),
                 extra={"search_parameters": {"mode": "off"}})
    _, _, _, body = xai.build(block, "q", None, "key")
    assert "search_parameters" not in body


def test_the_anthropic_workspace_header_is_sent_only_when_set(protocol,
                                                              monkeypatch):
    """An account detail, from the environment, never from the protocol."""
    monkeypatch.delenv(anthropic.WORKSPACE_ENV_VAR, raising=False)
    _, _, headers, _ = anthropic.build(protocol.block("claude"), "q", None, "k")
    assert "anthropic-workspace-id" not in headers

    monkeypatch.setenv(anthropic.WORKSPACE_ENV_VAR, "wrkspc_123")
    _, _, headers, body = anthropic.build(protocol.block("claude"), "q", None, "k")
    assert headers["anthropic-workspace-id"] == "wrkspc_123"
    assert "wrkspc_123" not in json.dumps(body), "a workspace is not measured"


def test_the_query_text_is_sent_verbatim(protocol):
    text = QUERIES[0]["text"]
    for module, system in ((anthropic, "claude"), (openai, "gpt-5"),
                           (xai, "grok")):
        _, _, _, body = module.build(protocol.block(system), text, None, "k")
        assert body["messages"][-1]["content"] == text
    _, _, _, body = google.build(protocol.block("gemini"), text, None, "k")
    assert body["contents"][0]["parts"][0]["text"] == text


def test_one_user_turn_and_no_prior_state(protocol):
    _, _, _, body = anthropic.build(protocol.block("claude"), "q", None, "k")
    assert body["messages"] == [{"role": "user", "content": "q"}]


# -- extraction is field access, and survives anything --------------------


@pytest.mark.parametrize("module", [anthropic, openai, google, xai])
@pytest.mark.parametrize("payload", [None, {}, [], "text", {"choices": []},
                                     {"content": "not a list"}])
def test_extract_never_raises(module, payload):
    read = module.extract(payload)
    assert read["text"] is None or isinstance(read["text"], str)


def test_gemini_joins_every_part(protocol):
    payload = {"candidates": [{"content": {"parts": [
        {"text": "one "}, {"text": "two"}]}}], "modelVersion": "g"}
    assert google.extract(payload)["text"] == "one two"


def test_anthropic_skips_non_text_blocks(protocol):
    payload = {"content": [{"type": "thinking", "thinking": "hidden"},
                           {"type": "text", "text": "shown"}]}
    assert anthropic.extract(payload)["text"] == "shown"


# -- credentials ----------------------------------------------------------


def test_the_key_never_reaches_the_transcript(protocol, monkeypatch):
    secret = "sk-ant-" + "z" * 40
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    session = FakeSession((200, ANSWER))
    step = run_mod.Step(query=QUERIES[0], system=systems_mod.by_id("claude"),
                        sequence=1)
    from runner.config import Config

    transcript = run_mod.converse(session, step, protocol, "r1", True,
                                  Config().resolved())
    assert secret not in json.dumps(transcript)
    assert transcript["request"]["headers"]["x-api-key"] == "<redacted>"


def test_a_key_echoed_back_by_an_api_is_redacted(monkeypatch):
    from runner.config import credential, redact

    secret = "sk-test-" + "q" * 30
    monkeypatch.setenv("XAI_API_KEY", secret)
    credential("XAI_API_KEY")
    assert secret not in redact(f"invalid key {secret} supplied")


def test_google_keeps_the_credential_out_of_the_url(protocol):
    _, url, headers, _ = google.build(protocol.block("gemini"), "q", None, "k")
    assert "key=" not in url
    assert headers["x-goog-api-key"] == "k"


# -- scope ----------------------------------------------------------------


def test_grok_is_out_of_published_scope():
    assert systems_mod.by_id("grok").published_scope is False
    assert systems_mod.by_id("grok").scope_note
    for name in ("claude", "gpt-5", "gemini"):
        assert systems_mod.by_id(name).published_scope is True


def test_published_selection_excludes_grok():
    chosen = [s.id for s in systems_mod.select(["published"])]
    assert "grok" not in chosen and len(chosen) == 3


def test_scope_travels_onto_the_transcript(protocol, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "k" * 20)
    session = FakeSession((200, json.dumps(
        {"model": "grok-4", "choices": [{"message": {"content": "hi"},
                                         "finish_reason": "stop"}]})))
    step = run_mod.Step(query=QUERIES[0], system=systems_mod.by_id("grok"),
                        sequence=1)
    from runner.config import Config

    transcript = run_mod.converse(session, step, protocol, "r1", True,
                                  Config().resolved())
    assert transcript["system"]["published_scope"] is False
    assert transcript["system"]["scope_note"]


# -- the constraint -------------------------------------------------------


#: "label" is deliberately absent: a system's display name is a label and is
#: not a judgment. The words here are the ones that would mean a transcript had
#: started holding an opinion about a response.
SCORING_WORDS = ("score", "scored", "grade", "rating", "rank", "ranked",
                 "verdict", "correct", "incorrect", "accurate", "hallucination",
                 "assessment", "assessed", "classification", "classified",
                 "flag", "flagged", "judgment")


def test_no_transcript_field_can_hold_a_judgment(protocol, monkeypatch):
    """The constraint made structural: there is nowhere for one to go."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    session = FakeSession((200, ANSWER))
    step = run_mod.Step(query=QUERIES[0], system=systems_mod.by_id("claude"),
                        sequence=1)
    from runner.config import Config

    transcript = run_mod.converse(session, step, protocol, "r1", True,
                                  Config().resolved())

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key.lower() not in SCORING_WORDS, f"{path}.{key}"
                walk(value, f"{path}.{key}")

    walk(transcript)


def test_the_run_records_that_nothing_assessed(tmp_path, protocol_dir, journal,
                                               runs, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    protocol = protocol_mod.load("v1", protocol_dir)
    register(journal, protocol.path, "prompt-protocol")
    path = write_set(tmp_path, queryset(**PILOT_SET))

    sent = FakeSession((200, ANSWER))
    monkeypatch.setattr(run_mod, "session_for", lambda: sent)

    code = main(["run", "--queryset", str(path), "--dir", str(protocol_dir),
                 "--journal", str(journal), "--pilot", "--systems", "claude",
                 "--runs", str(runs), "--no-seal", "--allow-unreachable"])
    assert code == 0
    directory = next(runs.iterdir())
    assert directory.name.startswith("pilot-")
    manifest = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert manifest["pilot"] is True
    assert "No model classified" in manifest["no_assessment"]
    assert transcript_mod.verify(directory)["chain_ok"]


def test_a_full_pilot_run_is_self_contained(tmp_path, protocol_dir, journal,
                                            runs, monkeypatch):
    """The run carries byte copies of both inputs, so it can be read alone."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    protocol = protocol_mod.load("v1", protocol_dir)
    register(journal, protocol.path, "prompt-protocol")
    path = write_set(tmp_path, queryset(**PILOT_SET))
    monkeypatch.setattr(run_mod, "session_for",
                        lambda: FakeSession((200, ANSWER)))

    main(["run", "--queryset", str(path), "--dir", str(protocol_dir),
          "--journal", str(journal), "--pilot", "--systems", "claude",
          "--runs", str(runs), "--no-seal", "--allow-unreachable"])
    directory = next(runs.iterdir())
    assert (directory / "protocol.snapshot.json").is_file()
    assert (directory / "queryset.snapshot.json").is_file()
    snapshot = json.loads((directory / "queryset.snapshot.json")
                          .read_text(encoding="utf-8"))
    assert snapshot["registrable"] is False
    assert snapshot["queries"][0]["text"] == QUERIES[0]["text"]


def test_a_query_with_no_text_is_refused(tmp_path, protocol_dir, journal, runs,
                                         monkeypatch):
    """A query with no text asks nothing and burns the item anyway."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    protocol = protocol_mod.load("v1", protocol_dir)
    register(journal, protocol.path, "prompt-protocol")
    document = queryset(**PILOT_SET)
    document["queries"] = [{"id": "neg-1", "category": "negated-parenthetical",
                            "text": None}]
    path = write_set(tmp_path, document)
    code = main(["run", "--queryset", str(path), "--dir", str(protocol_dir),
                 "--journal", str(journal), "--pilot", "--systems", "claude",
                 "--runs", str(runs), "--no-seal", "--allow-unreachable"])
    assert code == 2


# -- preflight: items are burned by being asked ---------------------------


def test_an_unreachable_system_is_found_before_anything_is_spent(protocol,
                                                                 monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    session = FakeSession((400, json.dumps({"error": {"message": "no"}})))
    ok, detail = run_mod.reachable(session, systems_mod.by_id("claude"),
                                   protocol, _config())
    assert ok is False
    assert "HTTP 400" in detail


def test_a_working_system_reports_its_model(protocol, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    ok, detail = run_mod.reachable(FakeSession((200, ANSWER)),
                                   systems_mod.by_id("claude"), protocol,
                                   _config())
    assert ok is True and detail == "claude-opus-5"


def test_a_200_with_no_assistant_turn_is_not_reachable(protocol, monkeypatch):
    """A key that works and a model that says nothing is still a broken run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    ok, detail = run_mod.reachable(FakeSession((200, json.dumps({"content": []}))),
                                   systems_mod.by_id("claude"), protocol,
                                   _config())
    assert ok is False
    assert "no assistant turn" in detail


def test_run_refuses_and_spends_nothing_when_a_system_will_not_answer(
        tmp_path, protocol_dir, journal, runs, monkeypatch):
    """The whole point: no run directory, no transcript, no item spent."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k" * 20)
    protocol = protocol_mod.load("v1", protocol_dir)
    register(journal, protocol.path, "prompt-protocol")
    path = write_set(tmp_path, queryset(**PILOT_SET))
    # A 400 rather than a 429: a 429 is retried by design, and this test is
    # about the refusal rather than the retry.
    monkeypatch.setattr(run_mod, "session_for",
                        lambda: FakeSession((400, "key not scoped")))

    code = main(["run", "--queryset", str(path), "--dir", str(protocol_dir),
                 "--journal", str(journal), "--pilot", "--systems", "claude",
                 "--runs", str(runs), "--no-seal"])
    assert code == 1
    assert not runs.exists(), "a refused run must leave nothing behind"


def _config():
    from runner.config import Config

    return Config().resolved()


def test_a_shadowed_credential_is_reported(tmp_path, monkeypatch):
    """An exported variable beating .env is correct, and must not be silent."""
    from runner.config import shadowed_credentials

    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=from-the-file\n"
                   "XAI_API_KEY=agreed\n"
                   "# a comment\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "from-the-environment")
    monkeypatch.setenv("XAI_API_KEY", "agreed")

    shadowed = shadowed_credentials(env)
    assert shadowed == ["OPENAI_API_KEY"], "only the disagreeing one"


def test_shadow_detection_returns_names_not_values(tmp_path, monkeypatch):
    from runner.config import shadowed_credentials

    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=file-secret\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-secret")
    reported = shadowed_credentials(env)
    assert "file-secret" not in str(reported)
    assert "env-secret" not in str(reported)


def test_a_pilot_run_directory_says_so_exactly_once():
    """The directory must carry the fact, and read like it was named on purpose."""
    assert transcript_mod.new_run_id("2026.Q4", pilot=True).startswith("pilot-2026.Q4-")
    assert transcript_mod.new_run_id("pilot-2026.09", pilot=True).startswith(
        "pilot-2026.09-")
    assert not transcript_mod.new_run_id("pilot-2026.09", pilot=True).startswith(
        "pilot-pilot")
    assert not transcript_mod.new_run_id("2026.Q4", pilot=False).startswith("pilot")
