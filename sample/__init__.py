"""Query selection: draw candidates, negate holdings, record ground truth.

Two categories, neither needing legal judgment to state a correct answer.

    metadata               the answer is a field in the corpus
    negated-parenthetical  the answer is the holding the query inverts

Both are reimplementations of categories in Magesh et al. (2024), whose
construction methods are reusable and whose items are not: their set has been
public and preregistered since 2024, so every system under test may have seen
it. `exclude.py` holds what we know of their items and says how much of their
set that covers.

**Selection is deterministic and recorded.** Candidates are ordered by a keyed
hash of the seed and their corpus identifier, not by a pseudo-random number
generator whose stream is an implementation detail. Every discarded candidate
is recorded with the rule that rejected it, because a walk that skips
candidates only reproduces if the skips reproduce.

Three commands in sequence, deliberately separate. `pool` scans the bulk drop.
`draw` selects, offline. `resolve` makes the API calls that write irreversible
journal records, and it runs only after a human has looked at the draw.

No model is in the loop. This filters rows, sorts by hash, and deletes the
word "not".
"""

__version__ = "0.1.0"

SCHEMA = "citationrecord.queryset.v1"
POOL_SCHEMA = "citationrecord.querypool.v1"

CATEGORIES = ("metadata", "negated-parenthetical")

#: Metadata question types. Authorship is API-only: the clusters `judges`
#: column is a free-text panel listing, not an attribution, and deriving an
#: answer from data that does not carry it is how an unsupportable figure got
#: into circulation once already.
METADATA_KINDS = ("year", "citation", "author")

#: How a candidate's position in the draw is computed. Stated so a third party
#: can recompute the order without reading this source.
DRAW_KEY = 'sha256(seed + ":" + category + ":" + candidate_id)'
