# `register/` — fix an artifact before it is used

Records the hash of an artifact in an append-only journal, so that a claim
about when it was fixed can be checked instead of believed.

**A control asserted after the fact is not a control.** A hash computed once
results exist is consistent with an artifact assembled to suit them, and no
reader can tell the two apart from outside. This component exists so the
ordering can be shown. The journal entry is the control; everything else
corroborates it.

It registers artifacts and does not know what they mean. A query set, its
ground truth, a prompt protocol, an item set and a methodology document are the
same thing here: bytes that must not change after a stated moment.

No model is in the loop. This hashes files and writes lines.

## Use

    py -m register add --artifact QUERIES.json --kind query-set --edition 2026.Q4
    py -m register verify --artifact QUERIES.json
    py -m register list --kind query-set
    py -m register chain

`add` is the one that matters, and **it runs before the first model call**. The
journal is written to `citation-resolutions/registrations.jsonl` by default,
alongside the lookup journal and public for the same reason: a control nobody
can read controls nothing.

Commit and push the journal after registering. A registration that exists only
on one machine is not yet a record of anything.

### Exit codes

    0   registered, or verified against a record
    1   already registered with identical content, or verification failed
    2   no such file, or a --supersedes naming no record
    3   the artifact is somewhere an unpublished artifact must not be

## The destination guard

The queries for an edition must not reach a public repository before that
edition ships. Publishing them in advance would make contamination of later
editions certain rather than possible, so this is a correctness rule and it is
enforced rather than remembered.

Two guards, because either alone leaks:

- **The named list** refuses `tooling`, `site`, `citation-resolutions` and
  `claim-archive`, by containment, so a subdirectory of any of them is refused
  too.
- **The generic check** refuses any git repository that is not the private
  artifact repository, which catches the ones nobody thought to name.

A path in no repository at all is allowed. The guard prevents publication, not
untidiness.

Exit 3 is deliberate and distinct from the other failures. A refusal here is
not a problem with the command; it is the command working.

## Two hashes

Every artifact gets both, and the record names which one controls.

| Hash | Over | Role |
| --- | --- | --- |
| `sha256_canonical` | sorted, compact, UTF-8 JSON, no trailing newline | the control |
| `sha256_bytes` | the file exactly as it sat on disk | what was there |

The byte hash alone is too fragile to be the control. Git normalises line
endings and editors add trailing newlines, and a byte hash that breaks for
those reasons is indistinguishable from one that breaks because the artifact
was altered. A control that cries wolf is not a control.

The canonical hash ignores every difference that does not change the data. It
exists only for artifacts that parse as JSON, which is why query sets are
written as JSON. Anything else falls back to bytes and records
`controls: bytes` so a reader knows which guarantee they have.

The canonical form is named in every record, as
`json/utf8/sorted-keys/compact/no-trailing-newline`, so a third party can
recompute it from the record without reading this source.

## The journal

One JSON object per line, exactly as `lookups.jsonl` does it.

Records are appended, never edited. An artifact that changes is registered
again, naming what it supersedes, and both records are kept. Registering
identical content twice is refused, since a second identical record states
nothing the first did not; `--again` overrides that when a second registration
is genuinely wanted.

Every record carries `prev_hash`, the SHA-256 of the preceding line, so
removing or rewriting a record breaks the chain at that point rather than
passing unnoticed. `py -m register chain` walks it and names the first break.

**The artifact's full path is not recorded.** The filename identifies it, and a
path can say more about an unpublished artifact than its owner intended.

## What it cannot do

It cannot prove that the registration came before the model call. Nothing local
can: the timestamp is this machine's, and the chain only orders records against
each other. What it does is make the claim checkable at all, and make a later
edit detectable. Pushing the journal promptly is what turns a local timestamp
into something a third party can bound.

It also cannot stop anyone registering an artifact after the fact and
presenting it as prior. It is a record, not a notary.

## Tests

    py -m pytest register/tests -q

Hermetic. No network and no real journal. The guard tests are the ones to read
first: they cover a public root, a subdirectory of one, an unnamed git
repository, the private repository, a path in no repository, and that the
refusal reaches the command rather than only the function. Two more assert
against the real configuration rather than a synthetic one, so a future edit
that empties the forbidden list fails a test instead of quietly permitting a
leak.
