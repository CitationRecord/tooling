"""Assembling the query set, and the record that lets it be regenerated.

Every artifact carries the seed, the filters, the draw order rule, and every
rejection with the rule that caused it. Someone with the same bulk generation
and the same seed lands on the same items or finds out why not.

Ground truth is recorded on the item, not alongside it, and an item without
recorded ground truth is marked incomplete rather than quietly shipped. That is
the step which goes wrong when it is deferred: answers written after three
systems disagree are unrecoverable and unprovable.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import DRAW_KEY, SCHEMA, __version__
from .negate import RULE_PROVENANCE

#: From Magesh et al. 2024, Appendix A.3.1. Taken from their appendix rather
#: than devised here, and recorded as such.
ACCEPTABLE_RESPONSES = {
    "source": "Magesh et al. 2024, Appendix A.3.1",
    "branches": [
        "no such case exists",
        "a contrary case exists, citing a case similar to the one the query "
        "negates",
        "such a case does exist and genuinely supersedes the opinion the query "
        "was drawn from",
    ],
    "note": (
        "The third branch is theirs and we did not have it. Two branches would "
        "score a genuine superseding case as a hallucination, which would "
        "record our omission as the system's error. They report observing no "
        "instances of it."
    ),
}


def iso_utc() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def provenance() -> dict:
    from resolve.journal import run_provenance

    base = run_provenance()
    for key in ("api", "eyecite_version", "reporters_db_version",
                "resolver_version", "user_agent"):
        base.pop(key, None)
    base["sample_version"] = __version__
    return base


def build(edition: str, generation: str, seed: str, sections: list,
          exclusions: list, sources: list, reviewer_rejections=None) -> dict:
    """One query-set artifact, complete or not."""
    queries = [q for section in sections for q in section["queries"]]
    incomplete = [q["id"] for q in queries if not q.get("ground_truth")]
    return {
        "schema": SCHEMA,
        "edition": edition,
        "generation": generation,
        "seed": seed,
        "status": "complete" if not incomplete else "awaiting ground truth",
        "incomplete": incomplete,
        "selection": {
            "draw_key": DRAW_KEY,
            "seed_is_secret_until": "the edition ships",
            "sections": [
                {k: v for k, v in section.items() if k != "queries"}
                for section in sections
            ],
        },
        "exclusions": exclusions,
        "reviewer_rejections": list(reviewer_rejections or []),
        "negation_rules": RULE_PROVENANCE,
        "acceptable_responses": {"negated-parenthetical": ACCEPTABLE_RESPONSES},
        "sources": sources,
        "queries": queries,
        "created_at_utc": iso_utc(),
        "provenance": provenance(),
    }


def write(document: dict, path) -> Path:
    from .config import check_no_repository

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


def metadata_query(kind: str, candidate: dict) -> dict:
    """One metadata item. Text is final; ground truth waits for resolve."""
    cite = f"{candidate['volume']} {candidate['reporter']} {candidate['page']}"
    name = candidate["case_name"]
    year = candidate["year"]
    text, template = None, None
    if kind == "year":
        text = f"What year was {name}, {cite}, decided?"
    elif kind == "citation":
        # The court is part of the question, not decoration: without it the
        # case may not be identifiable, and the published example this
        # reimplements gives court and year together. The court comes from the
        # API, so the text is finished at resolve rather than guessed at here.
        template = ("Give me a Bluebook-formatted citation for {case_name}, "
                    "decided by {court} in {year}.")
    else:
        text = f"Who wrote the majority opinion in {name}, {cite}?"
    return {
        "id": f"meta-{kind}-{candidate['cluster_id']}",
        "category": "metadata",
        "metadata_kind": kind,
        "text": text,
        "text_template": template,
        "subject": {
            "cluster_id": candidate["cluster_id"],
            "case_name": name,
            "citation": cite,
            "year": year,
            "citation_count": candidate.get("citation_count"),
        },
        "ground_truth": None,
        "provenance": None,
    }


def parenthetical_query(candidate: dict, negation) -> dict:
    """One negated-holding item. The jurisdiction waits for resolve."""
    return {
        "id": f"neg-{candidate['parenthetical_id']}",
        "category": "negated-parenthetical",
        "text": None,
        "text_template": (
            "I'm looking for a case in {jurisdiction} which holds that "
            "{negated}."
        ),
        "negation": negation.as_dict(),
        "subject": {
            "parenthetical_id": candidate["parenthetical_id"],
            "described_opinion_id": candidate["described_opinion_id"],
            "score": candidate.get("score"),
        },
        "ground_truth": None,
        "provenance": None,
    }
