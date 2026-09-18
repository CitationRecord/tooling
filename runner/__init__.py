"""Execute a frozen prompt protocol against base model APIs, and record.

`runner/` puts a fixed query to a system through its API as a raw model and
writes down exactly what came back beside exactly what was sent. That is the
whole job.

**No model classifies, scores, ranks or flags anything here.** The constraint
is not a preference to be traded against convenience later, so the component is
shaped so there is nowhere for an assessment to go: no field on a transcript
holds a judgment, and no code path reads a response body for any purpose except
storing it, extracting the assistant turn by field access, and hashing it.
Counting citations in a stored response is a measurement of text and is still
not done here; it happens in a separate pass, afterwards, over stored files.

**The protocol is a data file, not code.** `protocols/prompt-protocol-v1.json`
holds it, and its canonical hash is its identity. Code cannot be the protocol:
a refactor changes the bytes without changing what is sent, and a hash that
breaks for that reason is indistinguishable from one that breaks because the
protocol was altered. `register/` already carries `prompt-protocol` as a kind.

**A run refuses before it spends anything.** `runner/` will not call a model
against a protocol that is not registered, nor against a query set whose
registration state disagrees with the `--pilot` flag. Refusing is the
mechanism; the journal is only its record.

**Raw outputs are immutable.** Transcripts are created exclusively, hashed
after they are closed, chained in an append-only index, and set read-only. A
re-run is a new attempt and both are kept. Nothing is cleaned, normalised,
truncated or repaired: a malformed response is data.
"""

__version__ = "0.1.0"

SCHEMA = "citationrecord.runner.transcript.v1"
RUN_SCHEMA = "citationrecord.runner.run.v1"
PROTOCOL_SCHEMA = "citationrecord.runner.protocol.v1"

#: How the transcript index is chained, stated so a third party can recompute
#: it without reading this source.
CHAIN_KEY = "sha256(previous line, as written, utf-8)"

#: What happened on one attempt. A response is a response whatever it says:
#: an empty body, a refusal and a truncation are all `response`, because the
#: model answered and the answer is the datum. Only the transport failing, or
#: the API rejecting the request before a model saw it, is anything else.
OUTCOMES = ("response", "transport-failure", "rejected-by-api")

#: Whether the recorded request and response are the bytes that crossed the
#: wire, or a reconstruction. Declared on every transcript so the two can
#: never be mistaken for one another.
FIDELITY = ("captured", "reconstructed")
