# The Citation Record — Tooling

Data collection, verification, and report generation infrastructure for the Citation Record, an independent quarterly benchmark of citation accuracy in legal AI systems.

**Publication:** https://citationrecord.org
**Methodology:** [`CitationRecord/methodology`](https://github.com/CitationRecord/methodology)

---

## Purpose

This repository holds the code that produces each edition of the Citation Record. It is published so that the benchmark's results can be independently replicated.

Methodology is versioned separately. Tooling changes do not change the methodology version; methodology changes do. A result is reproducible when the methodology version, the tooling commit, and the measurement window are all stated — every published finding carries all three.

## Components

| Component | Purpose |
|---|---|
| `archive/` | Snapshot, hash, and log vendor claims for the system profiles in §7 |
| `resolve/` | Resolve citations against CourtListener; return reporter, volume, page, year, court, and subsequent history |
| `runner/` | Execute the frozen prompt protocol against base model APIs; capture raw outputs with version strings |
| `sample/` | Construct and stratify the test item set; produce the coverage gap analysis in §4.2 |
| `score/` | Panel scoring interface, blind to system identity; disagreement detection; inter-rater reliability |
| `build/` | Markdown to PDF, section-anchored HTML, executive summary, methodology hashing |

## Design constraints

These are stated controls published in the methodology, not implementation preferences. Code that violates one is a methodology defect.

**No AI system scores any item.**
Tooling prepares, presents, and records. Attorneys judge. Classes 1, 2, and 4a-4d resolve mechanically against ground truth with no model in the loop. Classes 3, 4e, and 5 require attorney adjudication and are never pre-labeled, suggested, or ranked by a model. Using an AI system to score AI systems on citation accuracy is a conflict the benchmark cannot survive.

**Raw outputs are immutable.**
Model responses are written once, hashed, and never edited. Scoring writes to a separate store keyed by item hash. An output that needs correcting is re-run, and both runs are retained.

**Every run records provenance.**
Model version string, API endpoint, UTC timestamp, prompt protocol version, methodology version. A result without provenance is not a result.

**Builds are deterministic.**
Each edition builds reproducibly from a commit. The methodology hash printed in the version statement must be independently recomputable by a third party.

**Nothing misrepresents itself.**
No account, request, or scrape identifies itself as anything other than what it is. No terms of use are accepted under a false description of purpose. This constraint outranks any data-collection objective.

## Excluded from this repository

**Test items.** The held-out item set and its ground-truth answers are stored on separate infrastructure. Git history survives deletion and forks survive a visibility change, so a single accidental commit would permanently defeat the held-out design described in methodology §3.2.

**Credentials.** API keys and tokens live in `.env`, which is ignored. Secret scanning and push protection are enabled organization-wide.

**Raw model outputs.** Stored off-repo and referenced by hash. They are large, regenerated each edition, and in some cases subject to vendor terms of use.

## Requirements

- Python 3.11+
- CourtListener API token - https://www.courtlistener.com/help/api/
- API credentials for the base models under test

## Setup

```powershell
git clone https://github.com/CitationRecord/tooling.git
cd tooling
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Add credentials to `.env` before running anything.

## Replication

Instructions for reproducing a published edition are in `REPLICATION.md`, alongside the tooling commit and methodology version for each edition released to date.

## Licensing

Code in this repository is licensed under Apache License 2.0. See `LICENSE`.

Published reports and the methodology document are licensed CC BY 4.0 in their own repositories.

## Contact

https://citationrecord.org
