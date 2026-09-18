# `runner/` — put a frozen query to a model, and write down what happened

Sends a fixed query to a system through its API as a raw model, and records the
response verbatim beside the request that produced it. That is the whole job.

**No model classifies, scores, ranks or flags anything here.** The component is
shaped so there is nowhere for an assessment to go: no transcript field can
hold a judgment, and no code path reads a response for any purpose except
storing it, hashing it, and reaching the assistant turn by field access. A test
walks a real transcript and fails if a scoring-shaped key ever appears in one.

Counting citations in a stored response is a measurement of text rather than an
assessment by a model, and it is *still* not done here. It happens afterwards,
separately, over files on disk.

## Use

    py -m runner protocols list
    py -m runner plan   --queryset SET.json --protocol v1 --systems all
    py -m runner probe  --systems all
    py -m runner run    --queryset SET.json --protocol v1 --systems all --pilot
    py -m runner verify --run RUN_ID

`protocols` and `plan` touch no network. `probe` sends one throwaway sentence
per system to check credentials and request shape, and records nothing. `run`
is the only command that spends a query, **and a query spent is a query
burned**: an item that has been put to a model can never appear in a published
edition.

### Exit codes

    0   done
    1   a transport failure left the run incomplete, or verification failed
    2   no such query set, protocol or run; or a query with no text yet
    3   the run directory is inside a repository
    4   the query set and the --pilot flag disagree
    5   the protocol is not registered

## The protocol is a data file

`protocols/prompt-protocol-v1.json`. Code cannot be the protocol: a rename or a
refactor changes the bytes without changing what is sent, and a hash that breaks
for that reason is indistinguishable from one that breaks because the protocol
was altered. A control that cries wolf is not a control — `register/` already
makes that argument about query sets, and already carries `prompt-protocol` as
a registrable kind.

The version string is a label. **The identity is the canonical hash**, computed
with `register/`'s own digest rather than a second implementation of it, since
two implementations of one canonical form would agree until the day they did
not, and that day would look exactly like tampering.

Three things hold it frozen:

- **The hash, at load.** Version, canonical hash and canonical form go onto
  every transcript. A result is not a result without the protocol version
  attached, and a version attached to nothing is a string.
- **The registration gate.** `run` refuses a protocol that is not in the
  journal, and exits 5. Refusing is the mechanism; the journal is its record.
- **Supersession, never editing.** A change means `prompt-protocol-v2.json` and
  a registration naming `--supersedes`. `load()` refuses a file whose internal
  `protocol_version` disagrees with its filename, which is what catches v1
  edited in place.

### What it cannot do

It cannot prove the protocol was frozen before the model was called. Nothing
local can: the timestamp is this machine's and the chain only orders records
against each other. It makes the claim checkable and a later edit detectable.
Pushing the journal promptly is what lets a third party bound the time. This is
`register/`'s own stated limit and repeating it more confidently would not
improve it.

## The gate, which refuses in both directions

|  | `run` | `run --pilot` |
| --- | --- | --- |
| Registered | runs | **refused** — a registered set is not a pilot |
| Unregistered, no pilot declaration | **refused** — this is the deadline | **refused** — no `pilot: true` |
| `pilot: true` and `registrable: false` | **refused** — a pilot must say so | runs, everything stamped |

A pilot declares itself twice, in the file and on the command line, so neither
a stray flag nor a forgotten one crosses the line by itself.

The unregistered-plain-set cell is the one that matters beyond the pilot. The
queries and their expected answers are fixed and hashed before any model is
called, and a hash computed afterwards demonstrates nothing: it is consistent
with a set assembled to suit the results, and no reader can tell the two apart
from outside. That ordering is now a refusal rather than a habit.

## Where responses are stored

Outside every git repository, under `../runs/`, guarded by
`register.config.check_no_repository` — the same function `sample/` reuses, not
a second copy of the rule. The tooling README excludes raw outputs from this
repository; this is where they go instead.

    runs/
      pilot-<edition>-<utc>-<short>/
        run.json                     what was asked, of whom, under what protocol
        protocol.snapshot.json       byte copy of the protocol used
        queryset.snapshot.json       byte copy of the set used
        transcripts/
          <query_id>__<system_id>__<attempt>.json
        transcripts.jsonl            append-only index, chained by prev_hash

A pilot run is named `pilot-…` so that a directory listing, a filename in a
report and a half-remembered path all carry the fact.

### Write-once, and what happens when a process dies

- Transcripts are created with `O_EXCL`. A second write to the same path fails
  rather than overwriting. A re-run is a new attempt and **both are kept**.
- The hash goes into the index only after the file is flushed, fsynced and
  closed. A process killed mid-write therefore leaves a file that fails
  verification loudly, rather than one whose hash was computed from what was
  meant to be written. A truncated transcript is not deleted: it is evidence.
- Every index line carries the hash of the line before it, as `lookups.jsonl`
  and `registrations.jsonl` do.
- The file is then set read-only. That stops accidents, not adversaries, and it
  is worth being clear which.

`verify` rehashes every transcript, walks the chain, lists anything unindexed,
and names every query-by-system cell that produced nothing.

## Stored raw

No cleaning, no normalising, no truncation, no repair. An empty response, a
malformed one, a refusal and one that stopped at the output ceiling are each
written exactly as they arrived, with the stop reason. **If a model returns
something broken, that is data.**

Nothing a model says is ever retried. Only a connection failure, a timeout, a
429 or a 5xx is retried, because none of those carries a response from a model,
and every attempt is recorded either way. A re-roll of an answer somebody did
not like is the exact failure this benchmark exists to detect in others.

## Why raw HTTP rather than four vendor SDKs

"The full request as sent" is a stronger claim than most clients can support.
An SDK builds the body itself, so recording what was handed to the SDK records
an intention rather than a request.

`requests` prepares the request before it goes out and keeps it on the
response, so `response.request.body` is the bytes that crossed the wire and
`response.content` is the bytes that came back. That buys **uniform `captured`
fidelity across all four systems**, instead of a guarantee that varies by
vendor, and adds no dependency this repository did not already have. Every
transcript declares its fidelity either way, because a record that cannot say
which it has is worth less than one that can.

The cost is that four request shapes are owned here rather than by a vendor.
`probe` exists so that a wrong one is found on a throwaway sentence instead of
on a pilot item.

## Sampling, and the parameter that does not exist

The provenance constraint asks for temperature and any other sampling
parameter. That field cannot be uniform across these four systems.

**Claude Opus 5 does not accept `temperature`, `top_p` or `top_k` at all.** They
were removed with the adaptive-thinking models and the API returns 400. There
is no temperature to record, and writing `temperature: 0` into a provenance
record because the protocol asked for one would be a false statement.

So each transcript keeps three things apart:

| Field | Means |
| --- | --- |
| `requested` | what the protocol asked for |
| `as_sent` | what actually appears in the request body |
| `unsupported` | what this vendor refuses, declared in advance |

A parameter a vendor silently drops shows up as a difference between the first
two, and is listed under `dropped`. A benchmark that believed it had set
temperature everywhere and had not would carry a hole nothing later could find.

There is therefore no single temperature for an edition. The per-system table
is the honest form and is published with the edition.

## Raw model, no retrieval layer

Not a matter of leaving something out. Each adapter names the exact request
setting that disables retrieval and that string is recorded on every
transcript, so a reader checks the claim against the stored request rather than
trusting the word "raw".

| System | Retrieval off by |
| --- | --- |
| Claude | no `tools` key at all — no web search, web fetch, MCP or code execution |
| GPT-5 | no `tools` key — no hosted web search, file search or retrieval |
| Gemini | no `tools` key — no `google_search` grounding |
| Grok | `search_parameters.mode` set to `off` **explicitly**, not left to a default |

Grok's is sent unconditionally rather than merged from the protocol, so that
turning retrieval off cannot be undone by a protocol edit. A default that
happens to be right today records nothing at all.

Anthropic's server-side `fallbacks` is deliberately **not** enabled. It routes a
refused request to a different model, which is sensible for an application and
wrong for a benchmark: a transcript labelled Claude that was answered by
another model destroys the only thing the record is for. A refusal is a result.

## Published scope

`systems.py` carries a `published_scope` flag. citationrecord.org commits to
three systems for Edition One: Claude, GPT-5 and Gemini. **Grok is `false`**,
with the reason attached to the system rather than to a report, so it cannot be
dropped in transit. The flag travels onto every transcript and index line, into
the run manifest under `systems_outside_published_scope`, and is printed by
`plan` before anything is sent.

The model identifier is *requested* by the protocol and *reported* by the API,
and both are recorded, because they can differ. A vendor silently serving a
different snapshot than the one asked for is a finding that is invisible unless
both are written down.

## Tests

    py -m pytest runner/tests -q

Hermetic. No network, no credentials, no real run directory. Read the gate
tests first: they cover every cell of the table above, including the two that
refuse in the direction nobody expects, and one asserts the refusal reaches the
command as exit 4 rather than only the function.

After those, the write-once tests, and the ones asserting that an empty,
malformed, refused or rejected response is stored verbatim and never retried —
which is where a convenience would do the most damage.
