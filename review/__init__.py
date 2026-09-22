"""The attorney review packet: drafted answers, and the authority under each.

**Nothing this component produces is ground truth.** Every answer in it is a
non-attorney's reading of published authority. A wrong one, published, would
say a system hallucinated when it answered correctly, which is the exact
failure this benchmark exists to measure and the worst place to commit it. The
artifact says so at file level, every item repeats it, and any rendered view
reads the warning from the artifact rather than restating it, so the two cannot
drift apart.

The design principle is about the reviewer's time. They are never asked *is
this right?*, which is a research question costing an hour an item. They are
asked *does this authority say what the answer claims it says?*, which is a
reading question against a named source and takes minutes.

That only works if every item carries its authority. An answer with no
resolvable authority does not go in the artifact; it goes on the unsourced
list, which is its own file and its own useful finding.

Two provenance paths, because the authorities are two kinds of thing:

    resolve/   a case, journalled as a citation lookup
    archive/   a document, snapshotted and hashed with its retrieval date

A local rule is not a case and has no citation for the resolver to touch. It is
a public document its publisher can amend, so it is archived the way a vendor
claim is, and the snapshot pins what it said on the day it was read.

The artifact carries registrable: false. register/ refuses it, and flipping
that flag means editing the file, where the change is visible in a diff.

No model is in the loop for selection or scoring. A person drafts, a person
reviews, and the component records both.
"""

__version__ = "0.1.0"

SCHEMA = "citationrecord.review.v1"
UNSOURCED_SCHEMA = "citationrecord.review.unsourced.v2"

#: How much work the answer represents, which tells a reviewer where to spend
#: their attention. Transcription is a document quoted; reading is a holding
#: summarised; interpretation is synthesis, and is the only one that genuinely
#: needs an attorney rather than a careful reader.
CONFIDENCE = ("transcription", "reading", "interpretation")

#: Which tier an item belongs to. The four interpretation-dependent items are
#: marked so a reviewer short on time knows which matter most.
TIERS = ("document-sourced", "interpretation-dependent")

CATEGORIES = ("local-rules", "circuit-split", "change-in-law", "doctrine")

#: How an item's authority was pinned.
PROVENANCE_PATHS = ("resolve", "archive")

#: Verdicts a reviewer may record.
VERDICTS = ("correct", "incorrect", "too loose to score")

#: Repeated at file level and on every item. One string, one place.
WARNING = (
    "Nothing in this artifact is ground truth. Every answer is a "
    "non-attorney's reading of published authority, drafted so that a "
    "reviewing attorney can check it against a named source rather than "
    "research it from scratch. An answer accepted without checking, and later "
    "published, would record a system as hallucinating when it answered "
    "correctly."
)

REGISTRABLE_REASON = (
    "This is a review packet pending attorney sign-off, not a query set. "
    "Registering it would fix content that is meant to change under review. "
    "The reviewed answers may later feed a query set, and that is what gets "
    "registered."
)
