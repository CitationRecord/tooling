"""Pre-registration: fix an artifact and record its hash before it is used.

A control asserted after the fact is not a control. A hash computed once
results exist is consistent with an artifact assembled to suit them, and no
reader can tell the two apart from outside. This component exists so the
ordering can be shown rather than claimed: the hash is written to an
append-only journal, timestamped and provenance-stamped, before the event it
constrains.

It registers artifacts. It does not know what they mean. A query set, a prompt
protocol, an item set and a methodology document are all the same thing here:
bytes that must not change after a stated moment.

Two hashes are recorded for every artifact. The byte hash is exactly what was
on disk. Where the artifact is JSON, a canonical hash over sorted, compact,
UTF-8 serialisation is recorded beside it and is the control, because a byte
hash breaks when git normalises a line ending and a broken control is
indistinguishable from a violated one.

The journal is append-only and chained: every record carries the hash of the
line before it, so a silent edit is detectable rather than merely discouraged.

No model is in the loop. This hashes files and writes lines.
"""

__version__ = "0.1.0"

SCHEMA = "citationrecord.register.v1"

#: What an artifact can be. Deliberately closed: an unrecognised kind is far
#: more often a typo than a new category, and a typo would file a registration
#: where nothing later looks for it.
KINDS = (
    "query-set",
    "ground-truth",
    "prompt-protocol",
    "item-set",
    "methodology",
)

#: Kinds that are published by design, and are therefore exempt from the
#: destination guard.
#:
#: The guard exists because an edition's queries must not reach a public
#: repository before that edition ships. That reasoning covers the artifacts
#: whose value depends on being unseen. It does not cover these two, which
#: citationrecord.org commits to publishing *before* the results they produce:
#: a prompt protocol nobody can read is not a published protocol, and a
#: methodology hash nobody can recompute controls nothing.
#:
#: Exempting them by kind rather than by a --force flag keeps the decision in
#: the code, where it is one line to read, instead of in whoever typed the
#: command.
PUBLISHABLE_KINDS = (
    "prompt-protocol",
    "methodology",
)

#: The canonical form used for the controlling hash, named in every record so
#: a third party can recompute it without reading this source.
CANONICAL_FORM = "json/utf8/sorted-keys/compact/no-trailing-newline"
