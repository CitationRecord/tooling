# `archive/` — claim archiver

Snapshots, hashes, and logs the public vendor claims tracked in the system
profiles of methodology §7.

A claim in a published profile has to be quotable months after the vendor
edits the page it came from. This component produces that evidence: a rendered
HTML snapshot, a screenshot, a SHA-256 over the HTML, a third-party copy in the
Wayback Machine, and an append-only manifest line tying them together. On every
later run it compares the new hash to the last one and flags the difference.

It does not judge claims. Whether a claim is accurate is attorney work.

## What it does not need

No credentials. No API keys, no accounts, no logins. Nothing in `.env` is read.
Every page it fetches is a page any member of the public can open, and the
archiver fetches it as itself.

## Install

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py -m playwright install chromium
```

## Use

```powershell
copy archive\claims.example.yaml archive\claims.yaml
py -m archive validate                 # parse the claim file
py -m archive capture --dry-run        # check robots.txt, fetch nothing
py -m archive capture                  # snapshot everything
py -m archive status                   # last state of each claim
py -m archive changes                  # every recorded hash change
py -m archive verify                   # recompute stored hashes
```

Useful flags on `capture`: `--only ID` (repeatable) to capture a subset,
`--no-wayback` to skip third-party submission, `--out DIR` to write elsewhere,
`--headed` to watch the browser work.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Every selected claim captured, no hash changed |
| 1 | Something was blocked by robots.txt, unverified, or failed to capture |
| 2 | At least one page hash changed since its last capture |

`capture`, `status`, and `changes` all use code 2 for "a tracked claim moved",
so a scheduled run can page a human on exactly that.

## Claim file

```yaml
version: 1
methodology_version: "1.0"
edition: "2026-Q3"

claims:
  - id: vendor-one-hallucination-free    # stable; becomes a directory name
    system: "Vendor One Research Assistant"
    vendor: "Vendor One, Inc."
    profile_ref: "7.1"
    url: https://vendor.example/product
    verified: true
    claim: >-
      Verbatim text of the assertion being tracked, quoted exactly as it
      appears on the page.
```

`id`, `claim`, and `verified` are required on every entry, and `url` is
required whenever `verified` is true. Everything else is optional. Unknown
keys are an error rather than a silent no-op, because a typo in a claim file
would otherwise produce a record that quietly tracks the wrong thing. Optional
per-claim overrides are `wait_until`, `settle_ms`, and `full_page`.

### `verified`

`verified` states whether the URL has been fetched and confirmed to be the
primary source of the claim. A claim reconstructed from an advertisement, a
screenshot, or trade coverage is not confirmed until someone locates it on the
vendor's own page.

An entry with `verified: false` is never fetched. No page request, no
screenshot, no robots.txt request, no Wayback submission. The run writes a
manifest record with status `unverified_source` and exits 1, so an unconfirmed
source stays visible instead of quietly disappearing from the run. A wrong
source in the evidence archive is worse than a missing one.

The flag is required and has no default. A forgotten key is a load error, not
silent permission to fetch. `url` may be omitted or null while `verified` is
false, for a claim whose primary source has not been located yet.

The claim file itself is hashed into every record it produces, so a record can
be traced to the exact list that generated it.

See `claims.example.yaml` for the shape. The real `claims.yaml` is **not** in
this repository and is gitignored so it cannot be committed here by accident.

The list annotates why each claim is tracked, and those annotations
characterize named vendors before they have been measured and before the
21-day notice the published methodology commits to. It therefore lives in
`CitationRecord/claim-archive`, which is private, and ships with the edition
it supports. Replicating a published edition means running this code against
that edition's claim list, which is released alongside it.

## Output layout

Output lives **outside** this repository. The default is `../claim-archive`,
resolved relative to the repository root, overridable with `--out`.

```
../claim-archive/
  manifest.jsonl
  snapshots/
    vendor-one-hallucination-free/
      20260903T001152Z/
        page.html      rendered DOM after scripts ran, UTF-8
        page.png       screenshot, full page by default
        page.txt       visible text, for the secondary text hash
        record.json    a copy of the manifest line, so the directory stands alone
      20260910T001204Z/
        ...
```

Snapshot directories are named for the UTC capture instant and are never
reused. Files are written once and then marked read-only.

## Manifest records

One JSON object per line, appended, never rewritten. Paths are relative to the
output directory so the archive can be moved or handed to a third party intact.

| Field | Meaning |
|---|---|
| `record_id`, `claim_id`, `captured_at_utc` | Identity of this capture, timestamp always UTC with an explicit `Z` |
| `url`, `final_url`, `http_status`, `content_type`, `page_title` | What was actually fetched, after redirects |
| `claim_text`, `claim_text_sha256` | The assertion being tracked, as of this run |
| `content_sha256`, `content_bytes` | SHA-256 over the rendered HTML, UTF-8 encoded. The primary hash |
| `text_sha256`, `screenshot_sha256` | Secondary hashes over visible text and the PNG |
| `paths` | Relative paths to `page.html`, `page.png`, `page.txt` |
| `change` | Comparison against the last successful capture of this claim |
| `robots` | The robots.txt URL, the verdict, the reason, any `Crawl-delay` |
| `wayback` | Submission status and the resulting snapshot URL |
| `verified_source` | The claim file's `verified` flag for this entry |
| `status`, `error` | `captured`, `blocked_by_robots`, `unverified_source`, or `fetch_error` |
| `provenance` | Archiver version, tooling commit and dirty flag, methodology version, claim file hash, user agent, Chromium build, Python version, platform |

### Two hashes, one signal

`content_sha256` is the one that flags a change, as specified. It is computed
over the full rendered HTML, which means it also moves for cache-busting query
strings, CSRF nonces, build identifiers, and rotating hero copy.

`text_sha256` covers `document.body.innerText` only. When the HTML hash moves
and the text hash does not, the markup churned and the words did not. Both are
reported; only the HTML hash drives the exit code.

## Design constraints

These come from the methodology, not from preference. Code that violates one
is a methodology defect.

**Nothing misrepresents itself.** The user agent is
`CitationRecordArchiver/<version> (+https://citationrecord.org; ...)`. It is
sent on the robots.txt request, on the page fetch, and on the Wayback
submission, and it is recorded in every manifest record. There is no browser
impersonation and no flag that turns it off.

**robots.txt is respected, with no override.** There is deliberately no
`--ignore-robots`. A disallowed URL is not fetched, not screenshotted, and not
submitted to a third-party archive; the refusal itself becomes the record, with
the reason. Following RFC 9309, a robots.txt that returns 4xx means no
restrictions, while one that is unreachable or returns 5xx means treat the
whole origin as disallowed. Requests to one origin are spaced by the greater of
`--delay` and any `Crawl-delay` the site declares.

**An unconfirmed source is never fetched.** See `verified` above. The refusal
happens before the robots.txt request, so an unconfirmed origin is not
contacted at all, and a run with nothing capturable does not even start a
browser.

**Captures are immutable.** The manifest is append-only and fsynced per line.
Snapshot files are opened `x`-exclusive and chmod'd read-only after writing. A
capture that needs correcting is re-run, and both runs are retained. `verify`
recomputes every stored hash against the manifest and reports drift.

**Every record carries provenance.** A record without the tooling commit,
archiver version, browser build, claim file hash, and UTC timestamp is not a
record.

**No model is in the loop.** Nothing here summarizes, classifies, scores, or
paraphrases a claim. It fetches bytes, hashes them, and writes down what it
did.

## Tests

```powershell
py -m pytest
```

Hermetic: no network, no browser. They cover claim file validation, change
detection, refusal to fetch an unverified source, manifest append-only
behavior and write-once files, Wayback URL parsing, exit codes, and the two
identity constraints above.

## Known limits

- **Client-side variance.** A page that renders A/B variants or rotates
  testimonials produces a new HTML hash every run. Check `change.text` before
  treating a flag as a claim edit, and pin such claims to a stabler URL if one
  exists.
- **Wayback is best-effort.** Save Page Now rate-limits and sometimes declines.
  A failure is recorded and never fails the capture; the local snapshot is the
  primary record. Some sites are excluded from the Wayback Machine entirely.
- **PDF claims.** A claim in a linked PDF is not captured by Chromium's
  renderer the way an HTML page is. Track the landing page for now.
- **Single writer.** Concurrent `capture` runs against one output directory are
  not coordinated. Run them one at a time.
