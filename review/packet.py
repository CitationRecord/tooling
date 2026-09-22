"""The item template, and the packet it assembles into.

An item is not finished when its answer is drafted. It is finished when the
authority under that answer is pinned, by a journalled lookup or a hashed
snapshot, and a reviewer can reach the passage the answer rests on. Anything
short of that is a plausible-sounding sentence, which is the thing this project
exists to catch.

So `item()` refuses to build an item with no authority, rather than building
one with an empty field that somebody fills in later.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import (
    CATEGORIES,
    CONFIDENCE,
    PROVENANCE_PATHS,
    REGISTRABLE_REASON,
    SCHEMA,
    TIERS,
    UNSOURCED_SCHEMA,
    WARNING,
    __version__,
)


class Unsourced(ValueError):
    """No authority could be pinned, so the question is not an item."""


def iso_utc() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def provenance() -> dict:
    from resolve.journal import run_provenance

    base = run_provenance()
    for key in ("api", "eyecite_version", "reporters_db_version",
                "resolver_version", "user_agent"):
        base.pop(key, None)
    base["review_version"] = __version__
    return base


def authority(kind: str, name: str, quote: str, path: str, **detail) -> dict:
    """One pinned authority.

    `quote` is the passage the answer rests on, verbatim. It is required: a
    reviewer asked whether an authority says what an answer claims needs the
    words, not a pointer to go and find them.
    """
    if path not in PROVENANCE_PATHS:
        raise ValueError(f"provenance path must be one of {PROVENANCE_PATHS}")
    if not (quote or "").strip():
        raise Unsourced(f"{name}: no quoted passage, so nothing to check against")
    return {"kind": kind, "name": name, "quote": quote.strip(),
            "provenance_path": path, **detail}


def item(item_id: str, tier: str, category: str, question: str,
         answer: str, confidence: str, authority_record: dict,
         question_source: dict, drafted_by: str,
         hedges=None, exclusion_check=None) -> dict:
    """One reviewable item, or a refusal to make one."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}")
    if confidence not in CONFIDENCE:
        raise ValueError(f"confidence must be one of {CONFIDENCE}")
    if not authority_record:
        raise Unsourced(f"{item_id}: no authority, so this is not an item")
    if tier == "interpretation-dependent" and not hedges:
        raise ValueError(
            f"{item_id}: an interpretation-dependent item must state what its "
            "drafter is unsure of. An unhedged interpretation reads as a fact.")

    return {
        "id": item_id,
        "tier": tier,
        "category": category,
        "status": "draft, pending attorney review",
        "warning": WARNING,
        "question": question,
        "drafted_answer": {
            "text": answer,
            "drafted_by": drafted_by,
            "drafted_at_utc": iso_utc(),
            "confidence": confidence,
            "hedges": list(hedges or []),
        },
        "authority": authority_record,
        "question_source": question_source,
        "exclusion_check": exclusion_check or {},
        "verdict": {"assessment": None, "notes": None},
        "reviewer": {"name": None, "date": None, "credit": None},
    }


#: Why a question could not be grounded. The distinction is not pedantry: one
#: of these says something about the law and the other says something about us,
#: and a list that blurs them would let a retrieval failure masquerade as a
#: finding about the categories.
UNSOURCED_REASONS = (
    "no authority found",          # nothing states the rule
    "authority not obtainable",    # it exists; no verifiable copy could be got
    "authority ambiguous",         # sources disagree and none is primary
    "excluded",                    # a prior benchmark already uses it
)


def unsourced(question: str, category: str, looked_for: str,
              why_not: str, reason_kind: str) -> dict:
    """A question that could not be grounded, recorded rather than dropped."""
    if reason_kind not in UNSOURCED_REASONS:
        raise ValueError(f"reason_kind must be one of {UNSOURCED_REASONS}")
    return {
        "question": question,
        "category": category,
        "looked_for": looked_for,
        "why_not": why_not,
        "reason_kind": reason_kind,
        "recorded_at_utc": iso_utc(),
    }


#: Why an entry leaves the unsourced list without becoming an item.
#:
#: Two outcomes, and they are not the same. "sourced" means the authority was
#: finally obtained and the question could now be drafted. "retired" means it
#: was obtained and the question is nonetheless spent, because the answer has
#: been published somewhere a system under test can read.
#:
#: A question published with its answer is burned in exactly the sense a
#: drawn query is burned by being asked: it can no longer measure retrieval,
#: only recall of our own writing. Recording the reason keeps a retirement
#: from reading later as a sourcing success.
RESOLUTION_KINDS = ("sourced", "retired")


def resolved(entry: dict, resolution_kind: str, authority_record: dict,
             note: str) -> dict:
    """An unsourced entry that has been settled, with what settled it.

    The original entry is carried whole rather than summarised. It recorded
    why the question could not be grounded, and that account stays readable
    beside the thing that finally grounded it -- otherwise the list loses the
    only evidence of how long the gap was open and what was tried.
    """
    if resolution_kind not in RESOLUTION_KINDS:
        raise ValueError(f"resolution_kind must be one of {RESOLUTION_KINDS}")
    if not authority_record:
        raise Unsourced("nothing resolved this: no authority record")
    for required in ("url", "sha256", "retrieved_at_utc"):
        if not authority_record.get(required):
            raise Unsourced(f"the authority record has no {required}")
    if not (note or "").strip():
        raise ValueError("a resolution must say what it means for the edition")
    return {
        "was_unsourced": dict(entry),
        "resolution_kind": resolution_kind,
        "authority": dict(authority_record),
        "note": note.strip(),
        "resolved_at_utc": iso_utc(),
    }


def packet(edition: str, drafted_by: str, items: list, exclusions: list,
           unsourced_file: str) -> dict:
    """The artifact. Declines registration in its own first fields."""
    by_tier = {tier: sum(1 for i in items if i["tier"] == tier)
               for tier in TIERS}
    by_path = {path: sum(1 for i in items
                         if i["authority"].get("provenance_path") == path)
               for path in PROVENANCE_PATHS}
    return {
        "schema": SCHEMA,
        "registrable": False,
        "registrable_reason": REGISTRABLE_REASON,
        "status": "draft, pending attorney review",
        "warning": WARNING,
        "edition": edition,
        "drafted_by": drafted_by,
        "counts": {"items": len(items), "by_tier": by_tier,
                   "by_provenance_path": by_path},
        "reviewer_guidance": (
            "You are not being asked whether each answer is right. You are "
            "being asked whether the authority quoted under it says what the "
            "answer claims it says. Where it does not, or where it is too "
            "loose to tell, say so in the verdict. The "
            "interpretation-dependent items are marked and are the ones worth "
            "your time if you run short."
        ),
        "exclusions": exclusions,
        "unsourced_list": unsourced_file,
        "items": items,
        "created_at_utc": iso_utc(),
        "provenance": provenance(),
    }


def unsourced_packet(edition: str, entries: list, resolved_entries=None) -> dict:
    resolved_entries = list(resolved_entries or [])
    retired = sum(1 for r in resolved_entries
                  if r.get("resolution_kind") == "retired")
    return {
        "schema": UNSOURCED_SCHEMA,
        "registrable": False,
        "registrable_reason": REGISTRABLE_REASON,
        "edition": edition,
        "note": (
            "Questions drafted for this edition that no authority could be "
            "found for. They are recorded rather than dropped: what could not "
            "be sourced says something about the categories, and a gap that "
            "leaves no trace gets filled with a guess next time."
        ),
        "resolved_note": (
            "Entries that have since been settled, kept here rather than "
            "deleted. A list that dropped them would lose the evidence of how "
            "long each gap was open and what finally closed it. A resolution "
            "marked `retired` did not become usable: the authority was "
            "obtained and the question is spent anyway, because its answer has "
            "been published where a system under test can read it."
        ),
        "counts": {"unsourced": len(entries),
                   "resolved": len(resolved_entries),
                   "retired": retired},
        "entries": entries,
        "resolved": resolved_entries,
        "created_at_utc": iso_utc(),
        "provenance": provenance(),
    }


def write(document: dict, path) -> Path:
    from sample.config import check_no_repository

    path = Path(check_no_repository(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return path


def read(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
