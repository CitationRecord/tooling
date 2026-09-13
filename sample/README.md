# `sample/` — draw queries with recorded ground truth

Selects candidates from the bulk drop, inverts holdings, and assembles a query
set whose selection can be regenerated from its own record.

Two categories, neither needing legal judgment to state a correct answer:
**metadata**, where the answer is a field in the corpus, and
**negated-parenthetical**, where the answer is the holding the query inverts.
Doctrine, bar-exam and circuit-split questions are out of scope here; they need
an attorney to state a correct answer.

This component holds the selection method. It holds no query text.

No model is in the loop. This filters rows, sorts by hash, and deletes the word
"not".

## Use

Three stages, deliberately separate.

    py -m sample pool --category metadata
    py -m sample pool --category negated-parenthetical
    py -m sample draw --edition 2026.Q4
    py -m sample show --draft query-drafts/queryset-2026.Q4-draft.json
    py -m sample resolve --draft query-drafts/queryset-2026.Q4-draft.json

`pool` scans the bulk drop and is the expensive half, so it is cached per
generation. `draw` selects and touches no network. `resolve` makes the API
calls that write irreversible journal records, and it runs only after somebody
has looked at the draw.

### Exit codes

    0   done
    2   a missing pool, a missing bulk file, or resolve not yet wired
    3   the destination is inside a repository

## Determinism

Candidates are ordered by a keyed hash, not by a pseudo-random number
generator:

    key(candidate) = sha256(seed + ":" + category + ":" + candidate_id)

`random.seed()` then `random.sample()` depends on Python's PRNG stream, which
is an implementation detail that can change between versions, and on the order
rows arrived in. A hash is defined by its specification, so the order can be
recomputed in any language from that one line. The candidate identifier breaks
ties, so two candidates cannot swap places because of read order.

**Every rejection is recorded with the rule that caused it.** A walk that skips
candidates only reproduces if the skips reproduce. Every check is therefore
mechanical rather than a judgment call, and the count of candidates examined
against candidates taken is in the artifact. If that ratio is high, the filters
were doing more work than the draw.

### The seed

Thirty-two random bytes, generated once per edition, written into the artifact,
and secret until the edition ships.

That is not fussiness. The bulk data is public, the filters are published as
methodology, and the edition identifier is public, so a seed derivable from any
of those would make the whole draw computable in advance by anyone.

**The seed window is a real limit and registration does not close it.**
Registering the artifact fixes the seed and the items together, so nothing can
be changed afterwards without breaking the hash. It says nothing about what
happened before. Someone could generate many seeds, look at the draws, and
register the one they preferred. Recording when the seed was generated makes a
reader aware of the window; it does not shut it. Registration bounds tampering
after the fact, not selection bias before it.

## Obscurity, operationalised

A question about *Brown* tests recall. A question about an unremarkable 1994
district court opinion tests retrieval. That preference is fields, not
judgment:

| Filter | Value |
| --- | --- |
| `citation_count` | at most 2 |
| `precedential_status` | Published |
| Reporter | in the preferred list, never a Supreme Court reporter |
| `date_filed` | present and parseable |

The lists live in `config.py` and every one of them is copied into the pool
file, so a pool that cannot say what population it drew from does not exist.

## Negation

Only ever by deletion. Inserting a negation into a positive holding is
unreliable: the scope of the inserted "not" is ambiguous, and the result is
often a different claim rather than a contrary one. A holding that already says
"does not apply" has isolated exactly what is being denied, so deleting the
denial inverts it cleanly.

Two named rules, `drop-not-after-auxiliary` and `cannot-to-can`. A candidate is
rejected unless it carries exactly one removable negator, survives removal with
no residual negation, sits in a single clause, and falls in the length band.
The rule name is recorded on the item it produced, beside the original and the
negation, so the transformation can be checked rather than trusted.

The pool is large enough that discarding is free and a doubtful negation is
not.

## Acceptable responses, and where they came from

The negated-parenthetical category scores against three branches, not two: no
such case exists, a contrary case exists, or such a case genuinely does exist
and supersedes the opinion the query was drawn from.

**The third branch is from Magesh et al. 2024, Appendix A.3.1, and we did not
have it.** Two branches would have scored a genuine superseding case as a
hallucination, recording our omission as the system's error. It is in the
artifact with its source attached, because it is theirs.

## Excluding prior benchmark items

Their construction methods are reusable. Their items are not: a set public
since 2024 may have been seen by every system under test.

`exclusions/*.json` holds what is known of those items, and **the check is
partial and says so**. The shipped list covers the 15 example queries printed
verbatim in the paper's Appendix A, out of 202. The full set is not publicly
obtainable: the OSF registration returns "Authentication credentials were not
provided" to the API, and the publisher returns 403 to automated requests.

The coverage figure travels in every artifact. A partial check that reports its
own coverage is worth more than an assertion of non-overlap from a filter, and
less than the real thing, and nobody should be able to mistake one for the
other later. When the full set arrives the data file grows and no code changes.

## Where things are written

Drafts and pools go outside every repository, including the private one they
will eventually live in. The rule is `register/config.py`'s, reused rather than
restated. Moving a finished set into `CitationRecord/queries` stays a
deliberate act.

## Tests

    py -m pytest sample/tests -q

Hermetic. No network and no bulk data. The ones to read first are the negation
rejections, since that is where a sloppy inversion would turn our error into a
recorded hallucination, and the exclusion tests, which assert that the shipped
list reports itself as partial.
