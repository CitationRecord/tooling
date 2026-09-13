"""Deterministic selection, and the record that makes it reproducible.

Candidates are ordered by a keyed hash of the seed and their corpus
identifier, not by a pseudo-random number generator. `random.seed()` then
`random.sample()` depends on Python's PRNG stream, which is an implementation
detail that may change between versions, and on the order rows arrived in. A
re-run years later would not be guaranteed to match. A hash is defined by its
specification rather than by a library, so anyone can recompute the order in
any language from one line of description.

    key(candidate) = sha256(seed + ":" + category + ":" + candidate_id)

Walk that order, take what survives the checks, and **record every rejection**.
A walk that skips candidates only reproduces if the skips reproduce, so every
check applied here has to be mechanical rather than a judgment call, and every
rejection is written down with the rule that made it.

The seed is random and secret until the edition ships. If it were derivable
from public information, so would the draw be: the bulk data is public, the
filters are published as methodology, and the edition identifier is public.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field


def new_seed() -> str:
    """Thirty-two random bytes. Recorded in the artifact, secret until it ships."""
    return secrets.token_hex(32)


def draw_key(seed: str, category: str, candidate_id) -> str:
    return hashlib.sha256(
        f"{seed}:{category}:{candidate_id}".encode("utf-8")
    ).hexdigest()


def ordered(candidates, seed: str, category: str, identify) -> list:
    """Every candidate, in draw order. Deterministic given the same inputs."""
    keyed = [(draw_key(seed, category, identify(c)), identify(c), c)
             for c in candidates]
    # The identifier breaks ties, so two candidates cannot swap places because
    # of the order they happened to be read in.
    keyed.sort(key=lambda item: (item[0], str(item[1])))
    return [item[2] for item in keyed]


@dataclass
class Draw:
    """What was taken, what was passed over, and why."""

    category: str
    seed: str
    pool_size: int
    wanted: int
    taken: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    examined: int = 0

    @property
    def complete(self) -> bool:
        return len(self.taken) >= self.wanted

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "pool_size": self.pool_size,
            "wanted": self.wanted,
            "examined": self.examined,
            "taken": len(self.taken),
            "rejected": self.rejected,
        }


def select(candidates, seed: str, category: str, wanted: int, identify,
           check=None) -> Draw:
    """Walk the draw order until `wanted` candidates survive `check`.

    `check` returns None to accept, or a short reason string to reject. It must
    be deterministic: anything that depends on the run rather than the
    candidate makes the draw unreproducible even with the record.
    """
    pool = ordered(candidates, seed, category, identify)
    result = Draw(category=category, seed=seed, pool_size=len(pool), wanted=wanted)

    for candidate in pool:
        if result.complete:
            break
        result.examined += 1
        reason = check(candidate) if check else None
        if reason:
            result.rejected.append({
                "candidate_id": str(identify(candidate)),
                "rule": reason,
            })
            continue
        result.taken.append(candidate)
    return result
