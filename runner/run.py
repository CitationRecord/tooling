"""One query, one conversation, one transcript. Nothing carried between them.

This module holds the gate and the loop. The gate is the interesting half.

**The gate refuses in both directions.** A query set that is registered may not
be run as a pilot, and a query set that is not registered may not be run at
all. The first stops a pilot being mistaken for an edition; the second is the
ordering commitment on citationrecord.org, enforced rather than remembered: the
queries are fixed and hashed before any model is called, and a hash computed
afterwards demonstrates nothing because it is consistent with a set assembled
to suit the results.

A pilot must declare itself twice, in the file and on the command line, so that
neither a stray flag nor a forgotten one can cross the line by itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import requests

from register.digest import digest_file, sha256_text
from register.journal import find_by_digest

from . import FIDELITY, OUTCOMES, RUN_SCHEMA, SCHEMA, __version__
from .adapters import wire as wire_mod
from .config import USER_AGENT, credential
from .protocol import Protocol
from .transcript import RunStore, iso_utc, new_run_id, provenance


class GateRefused(RuntimeError):
    """The query set and the --pilot flag disagree. Exit 4."""


class NotRegistered(RuntimeError):
    """The protocol is not in the registration journal. Exit 5."""


class MissingCredential(RuntimeError):
    """No API key for a system that was asked for."""


# -- the gate -------------------------------------------------------------


def queryset_state(document: dict) -> dict:
    """What a query set says about itself."""
    return {
        "declares_pilot": document.get("pilot") is True,
        "refuses_registration": document.get("registrable") is False,
        "registrable_reason": document.get("registrable_reason"),
        "edition": document.get("edition"),
    }


def gate(document: dict, registered: bool, pilot_flag: bool) -> None:
    """Refuse unless the file and the flag agree. Raises GateRefused."""
    state = queryset_state(document)

    if pilot_flag:
        if registered:
            raise GateRefused(
                "refusing to run --pilot against a registered query set. A "
                "registered set is a set that was fixed for an edition; "
                "running it as a pilot would burn the edition's items and "
                "record the result as exploratory.")
        if not state["declares_pilot"]:
            raise GateRefused(
                "refusing to run --pilot against a set that does not carry "
                "pilot: true at its top level. A pilot declares itself in the "
                "file as well as on the command line, so that a stray flag "
                "cannot turn a real set into a pilot.")
        if not state["refuses_registration"]:
            raise GateRefused(
                "refusing to run --pilot against a set that does not carry "
                "registrable: false. A pilot set must be one register/ will "
                "not fix, because its items are burned by being asked.")
        return

    if state["declares_pilot"] or state["refuses_registration"]:
        raise GateRefused(
            "refusing to run a pilot set without --pilot. The set declares "
            "itself a pilot, or declines registration, or both. Naming it on "
            "the command line is what keeps a pilot out of an edition by "
            "accident.")
    if not registered:
        raise GateRefused(
            "refusing to call a model against an unregistered query set.\n"
            "  The queries and their expected answers are fixed and hashed "
            "before any model is called, and that hash is written to an "
            "append-only record before the first query. A hash computed "
            "afterwards demonstrates nothing: it is consistent with a set "
            "assembled to suit the results, and no reader can tell the two "
            "apart from outside.\n"
            "  Register it first:  py -m register add --artifact <FILE> "
            "--kind query-set --edition <ID>")


def is_registered(path, journal, kind: str = "query-set") -> dict | None:
    """The journal record fixing this file, or None."""
    records = find_by_digest(journal, digest_file(path))
    for record in reversed(records):
        if record.get("kind") == kind:
            return record
    return records[-1] if records else None


# -- the plan -------------------------------------------------------------


@dataclass
class Step:
    query: dict
    system: object
    sequence: int
    attempt: int = 1


@dataclass
class Plan:
    protocol: Protocol
    queryset_path: Path
    document: dict
    systems: tuple
    steps: list = field(default_factory=list)
    pilot: bool = False
    protocol_record: dict = None
    queryset_record: dict = None

    @property
    def conversations(self) -> int:
        return len(self.steps)


def build_plan(document, queryset_path, protocol, systems, pilot) -> Plan:
    """Every conversation that would happen, in the order it would happen.

    Query-major: each query is put to every system before the next query. The
    sequence index is recorded on each transcript so the order is part of the
    record rather than an assumption about how the loop ran.
    """
    plan = Plan(protocol=protocol, queryset_path=Path(queryset_path),
                document=document, systems=tuple(systems), pilot=pilot)
    sequence = 0
    for query in document.get("queries", []):
        for system in systems:
            sequence += 1
            plan.steps.append(Step(query=query, system=system,
                                   sequence=sequence))
    return plan


def missing_credentials(systems) -> list:
    return [s for s in systems if not credential(s.env_var)]


def reachable(session, system, protocol, config) -> tuple:
    """One throwaway sentence, to find out whether this system will answer.

    **Items are burned by being asked, not by being answered.** A run against
    four systems where three reject the request spends every item in the set
    and returns a quarter of the data, and the set cannot be drawn again: those
    queries have been put to a model. A credential that exists but does not
    work, a model identifier a vendor has retired, an exhausted quota and a key
    scoped to the wrong workspace all look identical to a present credential
    and all cost the whole set.

    So `run` establishes that every system will answer before it asks anything
    that matters. The cost is one short request per system.
    """
    block = dict(protocol.block(system.id))
    block["max_output_tokens"] = 64
    key = credential(system.env_var)
    if not key:
        return False, f"{system.env_var} is not set"

    method, url, headers, body = system.adapter.build(
        block, "Reply with the word OK.", protocol.system_prompt, key)
    payload = json.dumps(body, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    wire = wire_mod.send(session, method, url,
                         {**headers, "user-agent": USER_AGENT}, payload,
                         timeout=min(config.timeout, 120.0), max_retries=1)

    if wire.transport_error:
        return False, wire.transport_error
    if not wire.ok:
        detail = (wire.response_body or "").strip().replace("\n", " ")
        return False, f"HTTP {wire.http_status}: {detail[:300]}"
    try:
        parsed = json.loads(wire.response_body)
    except json.JSONDecodeError:
        return False, f"HTTP {wire.http_status} but the body is not JSON"
    read = system.adapter.extract(parsed)
    if read.get("text") is None:
        return False, (f"HTTP {wire.http_status} but no assistant turn could "
                       f"be read from the response")
    return True, read.get("reported_model")


# -- sampling, recorded as three separate things --------------------------


def sampling_record(block: dict, body: dict) -> dict:
    """What was asked for, what actually went into the body, what is refused.

    Kept apart on purpose. A parameter a vendor silently drops shows up as a
    difference between `requested` and `as_sent`, and a benchmark that believed
    it had set temperature everywhere and had not would carry a hole nothing
    later could find.
    """
    declared = block.get("sampling") or {}
    requested = declared.get("requested") or {}
    nested = body.get("generationConfig") if isinstance(
        body.get("generationConfig"), dict) else {}

    as_sent = {}
    for name in requested:
        if name in body:
            as_sent[name] = body[name]
        elif name in nested:
            as_sent[name] = nested[name]
        else:
            as_sent[name] = None

    return {
        "requested": dict(requested),
        "as_sent": as_sent,
        "unsupported": list(declared.get("unsupported") or []),
        "note": declared.get("note"),
        "max_output_tokens": block.get("max_output_tokens"),
        "extra": dict(block.get("extra") or {}),
        "dropped": sorted(name for name, value in as_sent.items()
                          if value is None and requested.get(name) is not None),
    }


# -- one conversation -----------------------------------------------------


def outcome_for(wire) -> str:
    if wire.transport_error is not None:
        return "transport-failure"
    if wire.ok:
        return "response"
    return "rejected-by-api"


def converse(session, step, protocol, run_id, pilot, config,
             sleep=None) -> dict:
    """Put one query to one system, and return the transcript of doing so.

    A fresh request with no prior turns, no conversation identifier and no
    cached prefix. Nothing from any other query reaches this one.
    """
    system = step.system
    block = protocol.block(system.id)
    key = credential(system.env_var)
    if not key:
        raise MissingCredential(
            f"{system.env_var} is not set, so {system.label} cannot be "
            f"reached. Put it in tooling/.env or export it.")

    method, url, headers, body = system.adapter.build(
        block, step.query["text"], protocol.system_prompt, key)
    headers = {**headers, "user-agent": USER_AGENT}
    payload_bytes = json.dumps(body, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")

    extra = {}
    if sleep is not None:
        extra["sleep"] = sleep
    wire = wire_mod.send(session, method, url, headers, payload_bytes,
                         timeout=config.timeout,
                         max_retries=config.max_retries, **extra)

    # The response is parsed only to reach the assistant turn by field access.
    # Whatever happens here, the complete body is already on the wire record
    # and goes to disk verbatim.
    parsed = None
    if wire.response_body:
        try:
            parsed = json.loads(wire.response_body)
        except json.JSONDecodeError:
            parsed = None
    read = system.adapter.extract(parsed) if parsed is not None else {}

    wire_dict = wire.as_dict()
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "attempt": step.attempt,
        "sequence": step.sequence,
        "pilot": pilot,
        "query": {
            "id": step.query["id"],
            "category": step.query.get("category"),
            "metadata_kind": step.query.get("metadata_kind"),
            "text": step.query["text"],
            "edition": step.query.get("edition"),
        },
        "system": {
            **system.as_dict(),
            "requested_model": block["model"],
            "reported_model": read.get("reported_model"),
            "endpoint": wire_dict["request"]["url"],
        },
        "protocol": protocol.as_dict(),
        "request": wire_dict["request"],
        "response": {
            **wire_dict["response"],
            "sha256_bytes": sha256_text(wire.response_body or ""),
            "text": read.get("text"),
            "response_id": read.get("response_id"),
            "stop_reason": read.get("stop_reason"),
            "stop_detail": read.get("stop_detail"),
            "usage": read.get("usage"),
            "api_error": read.get("api_error"),
            "parsed_as_json": parsed is not None,
        },
        "sampling": sampling_record(block, body),
        "timing": wire_dict["timing"],
        "attempts_log": wire_dict["attempts"],
        "wire_fidelity": wire_dict["wire_fidelity"],
        "transport_error": wire_dict["transport_error"],
        "outcome": outcome_for(wire),
        "provenance": provenance(),
    }


def session_for() -> requests.Session:
    session = requests.Session()
    session.headers.update({"user-agent": USER_AGENT})
    return session
