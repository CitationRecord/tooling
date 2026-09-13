"""Rejections made by a reviewer rather than by a rule.

Some candidates are wrong in ways no mechanical check can catch without
reaching for legal knowledge, which is outside what this component is allowed
to do. Those are rejected by a person, and the decision is written down.

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

SCHEMA = "citationrecord.reviewed.v1"


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
        decided_by: str) -> dict:
    """Record one rejection. Appends; an existing decision is not overwritten."""
    path = Path(path)
    body = {"schema": SCHEMA, "rejections": entries(path)}
    if any(str(e["candidate_id"]) == str(candidate_id)
           for e in body["rejections"]):
        raise ValueError(f"{candidate_id} already has a recorded decision")

    entry = {
        "candidate_id": str(candidate_id),
        "category": category,
        "reason": reason,
        "decided_by": decided_by,
        "decided_at_utc": iso_utc(),
    }
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


def check(decisions: dict, category: str):
    """A draw-time check that consults recorded decisions.

    Returns the (reason, by) pair the draw records, so a reviewer rejection is
    never mistaken for a rule in the artifact.
    """
    def reviewed(candidate_id) -> tuple | None:
        entry = decisions.get(str(candidate_id))
        if entry is None or entry.get("category") != category:
            return None
        return (entry["reason"], f"reviewer:{entry['decided_by']}")
    return reviewed
