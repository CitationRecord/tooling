"""Drawing a pilot set, and keeping it apart from an edition's set.

A pilot exists to size the work downstream: how many citations a response
carries, how much judgment it takes to pull propositions out of one, what a
false-premise query actually elicits. None of that needs the edition's items,
and using them would destroy them, because **an item put to a model is burned**.
It has been seen, and a published edition cannot contain it.

So a pilot draws from the same pools by the same method under a different seed,
and three things keep the two apart.

**The items are excluded by rule.** A different seed almost certainly lands
elsewhere, and "almost certainly" is not a control. The edition's drawn
candidate identifiers are read out of its artifact and rejected at draw time
like any other rule, so the rejection appears in the pilot's own record with
the rule that caused it. Nothing here reads the edition's seed, queries or
ground truth.

**The seeds are provably different.** The pilot artifact records the SHA-256 of
the edition's seed beside its own. A reader with both artifacts can check that
the pilot seed is not the edition seed, without the pilot artifact ever
carrying the edition seed -- which is secret until the edition ships.

**The artifact refuses to be registered.** It carries `pilot: true` and
`registrable: false` with a reason, so `register/` exits 4 on it and `runner/`
will only run it with `--pilot`. A pilot that could be registered is a pilot
that could be mistaken for an edition.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: Why a pilot set declines registration. `register/` prints this verbatim.
REGISTRABLE_REASON = (
    "This is a pilot set, not an edition's query set. Its items are burned: "
    "they have been put to models and must never appear in a published "
    "edition. Registering it would fix an artifact whose only purpose was to "
    "be spent, and would put a pilot in the same journal as the pre-"
    "registration records that control the editions."
)

#: Stated at file level on every pilot artifact.
WARNING = (
    "Nothing in this artifact is Edition One. These queries were drawn from "
    "the same pools by the same method under a different seed, to size the "
    "work that follows a run. Every item in it is burned by the run it was "
    "drawn for: it has been put to models, so it can never appear in a "
    "published edition. Results from it are exploratory and must not be "
    "published, cited, or compared with an edition's results."
)

#: The rule that rejects an edition's item from a pilot draw. Recorded on the
#: candidate it rejected, like every other rule.
EXCLUSION_RULE = "drawn for an edition already; excluded from the pilot by rule"


def seed_fingerprint(seed: str) -> str:
    """SHA-256 of a seed, so two seeds can be compared without disclosing one."""
    return hashlib.sha256((seed or "").encode("utf-8")).hexdigest()


def drawn_identifiers(paths) -> dict:
    """Every candidate identifier already drawn, by category, from artifacts.

    Reads only the identifiers. The queries, their ground truth and the seed
    are not touched, and nothing from them reaches the pilot artifact beyond
    the fact that an identifier was taken.
    """
    # Kept as sorted lists rather than sets: this structure sits next to an
    # artifact, and anything near an artifact should serialise without a
    # special case waiting to be forgotten.
    taken = {"metadata": set(), "negated-parenthetical": set()}
    sources = []
    for path in paths or ():
        path = Path(path)
        document = json.loads(path.read_text(encoding="utf-8"))
        counts = {"metadata": 0, "negated-parenthetical": 0}
        for query in document.get("queries", []):
            category = query.get("category")
            subject = query.get("subject") or {}
            identifier = (subject.get("cluster_id")
                          if category == "metadata"
                          else subject.get("parenthetical_id"))
            if category in taken and identifier is not None:
                taken[category].add(str(identifier))
                counts[category] += 1
        sources.append({
            "filename": path.name,
            "edition": document.get("edition"),
            "seed_sha256": seed_fingerprint(document.get("seed")),
            "identifiers_excluded": dict(counts),
            "read": "candidate identifiers only; not the seed, queries or "
                    "ground truth",
        })
    return {
        "by_category": {category: sorted(ids)
                        for category, ids in taken.items()},
        "sources": sources,
    }


def check(taken: dict, category: str):
    """A draw-time check rejecting anything an edition already took."""
    excluded = set(taken.get("by_category", {}).get(category) or ())

    def reject(candidate_id):
        if str(candidate_id) in excluded:
            return EXCLUSION_RULE
        return None

    return reject


def decorate(document: dict, taken: dict, label: str) -> dict:
    """Stamp a drawn artifact as a pilot, unmistakably and at top level.

    `registrable: false` is what `register/` reads. `pilot: true` is what
    `runner/` reads. Both are required together before a pilot can be run, so
    that neither flag alone can move a set across the line.
    """
    document["pilot"] = True
    document["registrable"] = False
    document["registrable_reason"] = REGISTRABLE_REASON
    document["warning"] = WARNING
    document["items_are_burned"] = (
        "These items are burned. Every query in this set has been put to "
        "models, or is drawn in order to be, and a query a model has seen is "
        "spent: it can never appear in a published edition of The Citation "
        "Record. This is not a caution about what might happen. It is a "
        "statement about what this artifact is for.")
    document["pilot_label"] = label
    document["seed_role"] = (
        "Pilot draw seed. This is NOT the seed of any edition, and no "
        "edition's draw can be reproduced from it.")
    document["seed_sha256"] = seed_fingerprint(document.get("seed"))
    document["distinct_from"] = {
        "note": "The SHA-256 of each excluded artifact's seed, so a reader "
                "holding both can check the two draws used different seeds "
                "without this artifact ever carrying the other one.",
        "sources": taken.get("sources", []),
    }
    document["excluded_prior_draws"] = {
        "rule": EXCLUSION_RULE,
        "why": "A different seed almost certainly lands on different items, "
               "and almost certainly is not a control. A collision would burn "
               "an item an edition is holding, so the edition's identifiers "
               "are rejected by rule and the rejection is in the record.",
        "counts": {category: len(ids) for category, ids
                   in taken.get("by_category", {}).items()},
    }
    return document
