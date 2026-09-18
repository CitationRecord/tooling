"""Rejections the draw is told about, by a person or by a named condition.

Some candidates are wrong in ways no mechanical check can catch without
reaching for legal knowledge, which is outside what this component is allowed
to do. Those are rejected by a person, and the decision is written down.

Others are wrong for a reason that is entirely mechanical and simply cannot be
seen at draw time, because the fact lives in an API rather than in the pool.
**Those must not be recorded as anybody's judgment.** This module already
argues that a reviewer's decision should never read as something a rule caught;
the same argument runs in reverse, and a data gap filed under a person's name
misattributes the one thing the record exists to keep straight.

So a rejection carries a source. A reviewer decision names who made it. A
rule-sourced one names the condition instead, from a closed list, along with
the stage at which it was discovered. Both appear in the draw's rejection
record, and a reader can tell at a glance which is which.

**A judgment call would break the draw if it stayed a judgment call.** The whole
guarantee is that the same bulk generation and the same seed land on the same
items, and a reviewer deciding live during a walk destroys that. So the decision
is recorded first and consulted as data: the draw reads this file the way it
reads the exclusion list, and a rerun with the same file reproduces exactly.

That makes the file part of the record rather than a note beside it. It is
copied into the query-set artifact, so anyone checking the selection sees which
candidates a person removed, why, and when, and can tell those apart from what a
rule removed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "citationrecord.reviewed.v2"

#: Where a rejection came from.
#:
#: "reviewer" is a person's judgment and names them. "rule" is a mechanical
#: condition and names the condition instead of a person, because nobody
#: judged anything.
SOURCES = ("reviewer", "rule")

#: Mechanical conditions the draw can be told about after the fact.
#:
#: A closed list, for the reason `register/` keeps its kinds closed: an
#: unrecognised condition is far more often a typo than a new category, and a
#: typo would file a rejection under a name nothing later recognises. Each
#: entry states what the condition is and why it could not be seen at draw
#: time, so the record explains itself without this source.
CONDITIONS = {
    "author-absent-in-courtlistener": {
        "what": (
            "CourtListener records no author for the opinion carrying the "
            "judgment: `author` is null, `author_str` is empty, and the "
            "cluster's `judges` field is empty."
        ),
        "why_not_at_draw": (
            "No field in the metadata pool carries an author. The pool is "
            "built from the bulk citations and clusters tables, and "
            "authorship lives on the opinions endpoint, so the absence is "
            "only visible once the item is resolved."
        ),
        "discovered_at": "resolve",
        "affects": "the authorship question only",
    },
}


def iso_utc() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def path_for(workdir, edition: str) -> Path:
    return Path(workdir) / f"reviewed-{edition}.json"


def load(path) -> dict:
    """Reviewer decisions, keyed by candidate id. Missing file reads empty."""
    path = Path(path)
    if not path.is_file():
        return {}
    body = json.loads(path.read_text(encoding="utf-8"))
    return {str(entry["candidate_id"]): entry
            for entry in body.get("rejections", [])}


def entries(path) -> list:
    """Decisions in the order they were made, for the artifact."""
    path = Path(path)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("rejections", [])


def add(path, candidate_id: str, category: str, reason: str,
        decided_by: str = None, source: str = "reviewer",
        condition: str = None) -> dict:
    """Record one rejection. Appends; an existing decision is not overwritten.

    A reviewer decision names who made it. A rule-sourced one names the
    condition from `CONDITIONS` instead, and carries that condition's own
    account of itself, so the artifact explains the rejection without anyone
    reading this module.
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    if source == "rule":
        if condition not in CONDITIONS:
            raise ValueError(
                f"unknown condition {condition!r}; known: "
                f"{', '.join(sorted(CONDITIONS))}")
        if decided_by:
            raise ValueError(
                "a rule-sourced rejection names a condition, not a person. "
                "Recording a mechanical fact under someone's name is the "
                "confusion this field exists to prevent.")
    elif not decided_by:
        raise ValueError("a reviewer decision must name who made it")

    path = Path(path)
    body = {"schema": SCHEMA, "rejections": entries(path)}
    if any(str(e["candidate_id"]) == str(candidate_id)
           for e in body["rejections"]):
        raise ValueError(f"{candidate_id} already has a recorded decision")

    entry = {
        "candidate_id": str(candidate_id),
        "category": category,
        "reason": reason,
        "source": source,
        "decided_by": decided_by,
        "decided_at_utc": iso_utc(),
    }
    if source == "rule":
        entry["condition"] = condition
        entry["condition_detail"] = dict(CONDITIONS[condition])
        entry["discovered_at"] = CONDITIONS[condition]["discovered_at"]
    body["rejections"].append(entry)

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(body, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return entry


def attribution(entry: dict) -> str:
    """How the draw's rejection record names the source of one decision.

    Three shapes appear in an artifact, and they are deliberately distinct:

        rule                          a draw-time rule, recorded by draw.select
        rule:<condition>              a mechanical condition found later
        reviewer:<name>               a person's judgment

    An entry written before this module carried a source is a reviewer
    decision, because that is all there was.
    """
    if entry.get("source") == "rule":
        return f"rule:{entry.get('condition')}"
    return f"reviewer:{entry.get('decided_by')}"


def check(decisions: dict, category: str):
    """A draw-time check that consults recorded decisions.

    Returns the (reason, by) pair the draw records, so a reviewer rejection is
    never mistaken for a rule in the artifact, and a mechanical condition is
    never mistaken for somebody's judgment.
    """
    def reviewed(candidate_id) -> tuple | None:
        entry = decisions.get(str(candidate_id))
        if entry is None or entry.get("category") != category:
            return None
        return (entry["reason"], attribution(entry))
    return reviewed
