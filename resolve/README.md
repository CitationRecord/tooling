# `resolve/` — CourtListener citation resolver

Resolves a case citation against CourtListener and returns the reporter,
volume, page, year, court, and what subsequent history CourtListener carries.

The point of this component is the outcome it reports. There are three, not
two.

## Three outcomes

| Outcome | Meaning | Scoring |
|---|---|---|
| `resolved` | CourtListener holds the cited case | Class 1 |
| `not_found` | CourtListener holds the cited **volume** and there is nothing at that page | Class 1 |
| `not_covered` | CourtListener does not hold the reporter or the volume, or it is not a US case citation | Unscorable |

`not_found` is the only outcome that supports a fabrication finding. A
citation that CourtListener simply does not cover says nothing about the
system that produced it, and scoring it as an error would inflate error rates
against systems drawing on corpora broader than CourtListener's. The third
state feeds the unscorable bucket for attorney adjudication, not the Class 1
count.

Two further states are **not findings about the citation** and must never be
scored as one:

| State | Meaning |
|---|---|
| `ambiguous` | More than one case matches. An attorney resolves it |
| `error` | Rate limit, network failure, bad token. Re-run it |

An error is never cached, so a throttled request cannot harden into a
permanent finding.

## How the distinction is actually made

CourtListener's citation lookup returns 404 for both a fabricated citation
and one in a reporter it has never ingested. The 404 alone cannot tell them
apart, so the resolver asks a second question before it labels anything.

```
citation-lookup returns a cluster            -> resolved
citation-lookup returns 404                  -> probe coverage:
      CourtListener indexes opinions in this volume    -> not_found
      it indexes the reporter but not this volume      -> not_covered
      it indexes nothing in this reporter              -> not_covered
citation-lookup parses nothing                -> not_covered
      unknown reporter, statute, law review, regulation
the call failed                               -> error
```

The coverage probe is empirical, not assumed. It asks CourtListener how many
opinions it indexes for the cited volume, and if none, for the reporter as a
whole, and records those counts in the result so a "not found" can be
audited. Counts are cached per reporter and per volume because they are
shared by every citation into the same book.

`--no-coverage-probe` skips it and saves two throttled calls per miss. With
the probe off, **every** miss is reported as `not_covered`, because without
coverage evidence a fabrication cannot be distinguished from a gap. The
resolver will not guess in the direction that produces a finding.

### Where this is still conservative

When CourtListener holds a reporter but not the cited volume, the result is
`not_covered` even though an invented volume number would look identical. The
two cannot be separated from CourtListener alone, so the citation goes to the
unscorable bucket with its coverage counts attached and an attorney decides.
That is the direction the methodology requires: a coverage gap scored as a
fabrication is worse than a fabrication left unscored.

## Credentials

The CourtListener API token is read from the environment and nowhere else.

```powershell
copy .env.example .env      # then paste your token into COURTLISTENER_API_TOKEN
```

Get a token at https://www.courtlistener.com/help/api/.

There is deliberately no `--token` flag: a credential passed as an argument
lands in shell history and process listings. The token is never written to
the cache, the journal, or an error message. Every string headed for the log
passes through a redactor first, so a traceback carrying a URL with
credentials cannot leak through. Two tests assert both of these.

The resolver refuses to run unauthenticated rather than falling back to
anonymous requests, which are throttled hard enough that the failures would
look like coverage gaps.

## Use

```powershell
py -m resolve cite "576 U.S. 644"           # one or more citations
py -m resolve cite "9999 U.S. 1" --json     # full record
py -m resolve batch citations.txt           # one per line, # comments ignored
py -m resolve log --tail 20                 # the lookup journal
py -m resolve cache --stats                 # what is cached
```

Useful flags: `--refresh` re-queries past the cache, `--no-cache` neither
reads nor writes it, `--max-age DAYS` sets how long a negative result stays
cached, `--methodology VERSION` stamps the journal.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Everything resolved, or the only misses were coverage gaps |
| 1 | A lookup errored: rate limit, network, token |
| 2 | At least one citation was not found in CourtListener |

Coverage gaps alone exit 0. They are not findings against a system.

## What comes back

```
reporter, volume, page      as held by CourtListener, plus what was cited
year                        from date_filed, not from the citation
court                       full name and CourtListener court id
case_name, cluster_id, courtlistener_url
parallel_citations          every citation CourtListener has for the case
subsequent_history          see below
discrepancies               the cite resolves but something in it disagrees
coverage                    the evidence behind a not_found or not_covered
```

### Subsequent history, and its limits

CourtListener carries no Shepard's or KeyCite style treatment signal. There
is no "overruled" or "questioned" flag to read. What it has is the history
text of the source record, which is frequently empty, plus the precedential
status and a count of citing cases:

```
history, disposition, procedural_history, other_dates,
cross_reference, correction, precedential_status, citation_count
```

Every resolution carries a `caveat` field saying this in the data itself, so
an empty history cannot be read downstream as a finding that the case is good
law. Determining current validity is outside what this API supports and is
attorney work.

### Discrepancies

When a citation resolves but part of it disagrees with the case, that is
recorded rather than ignored. A cited year that differs from the filing year
is a real citation-accuracy finding even though the case exists. An opinion
CourtListener marks unpublished is flagged for the same reason.

## Cache

SQLite in the output directory, default `../citation-resolutions`:

```
../citation-resolutions/
  resolutions.sqlite3     resolutions, reporter and volume coverage, court names
  lookups.jsonl           append-only journal, one line per lookup
```

Resolved cases are cached indefinitely; a decided case does not change.
Negative results expire after 30 days by default, because CourtListener
ingests opinions continuously and a "not found" from months ago may simply be
older than the data. Errors are never cached. Every entry records the UTC time
it was retrieved, so a cached answer can always be dated against an edition's
measurement window.

## Journal

Every lookup is logged, cache hits included, one JSON object per line,
appended and never rewritten. Each entry carries a UTC timestamp, the query,
the outcome and its reason, whether it was scorable, the coverage evidence,
the HTTP calls made with their durations and throttle status, and run
provenance: resolver version, tooling commit and dirty flag, methodology
version, eyecite and reporters-db versions, Python version, platform.

A result without provenance is not a result.

## Rate limits

Observed on a personal token: the search endpoint allows about 5 requests per
minute, the citation lookup considerably more. The client throttles per
endpoint, honours `Retry-After` and the wait hint in the throttle reply, and
retries with backoff. A coverage probe costs up to two search calls, which is
why coverage counts are cached per book rather than per citation.

A run over many citations in unfamiliar reporters will spend most of its time
waiting on the search limit. Cached coverage makes the second run fast.

## Parsing

Citations are parsed with eyecite, which is what CourtListener itself parses
with, so a citation this module accepts is one the API will recognise.
Reporters are checked against reporters-db, the same list eyecite is built on.
A regex fallback handles input eyecite declines.

Pass one citation per query. eyecite attaches a trailing year parenthetical to
whichever citation precedes it, so a string holding several citations yields
the wrong year for all but the last.

## Tests

```powershell
py -m pytest resolve/tests
```

Hermetic: no network, no token, no live API. They cover the three-outcome
discrimination, the coverage probe's three verdicts, refusal to report
`not_found` without coverage evidence, rate limits never becoming a missing
citation, errors never being cached, negative results expiring while
resolutions do not, the token never reaching the journal, and the absence of
a token command-line flag.
