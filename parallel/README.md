# `parallel/` — reporter coverage counted per cluster

Counts how many cases carry a citation in one reporter, in another, and in
both.

A reporter-by-reporter reading of the citations table overstates missing
coverage wherever a case is held under a parallel citation. California is the
clearest instance. An opinion may carry a Cal. Rptr. 3d citation, a Cal. App.
5th citation, or both, and counting one reporter alone cannot tell an absent
case from a case indexed under the other name.

This counts clusters, not citations, so the two readings can be compared
directly instead of one standing in for the other.

**It counts citations, not cases.** A cluster carrying no citation in a
reporter is not an absent case. Nothing here establishes that any case is
missing from CourtListener. Every artifact says so first among its caveats.

No model is in the loop. This counts rows and reports arithmetic.

## Use

Fetch the generation first, then count a pair:

    py -m bulk fetch --only citations
    py -m parallel pair --reporter "Cal. Rptr. 3d" --against "Cal. App. 5th"

One pass over the citations table, about a minute and a half for the June 30
generation. The artifact lands under `results/` unless `--out` says otherwise.

    --generation YYYY-MM-DD   bulk generation (default: the current one)
    --dir PATH                bulk directory, if not the generation's
    --out FILE                artifact path
    --json                    also print the artifact
    --quiet                   no progress on stderr

### Exit codes

    0   counted, artifact written
    1   a reporter matched no citation row
    2   no citations file for that generation

Exit 1 is deliberate. A reporter name that matches nothing is far more often a
misspelling than an absence, so the run fails with the spelling named rather
than writing an artifact around a zero that reads as a finding.

## What it measures

For reporters A and B, over every citation row in the generation:

| Field | Meaning |
| --- | --- |
| `clusters.a_only` | clusters carrying A and no B |
| `clusters.b_only` | clusters carrying B and no A |
| `clusters.both` | clusters carrying both |
| `clusters.either` | clusters carrying at least one |
| `citation_rows.a` | A citations, however many per cluster |
| `citation_rows.without_cluster` | matching citations naming no case |

A cluster carrying six citations in one reporter counts once. Citations with
no cluster are counted separately rather than dropped, because they name no
case and cannot be attributed to one.

`arithmetic.a_only_share_of_a` is the share of A's clusters carrying no B
citation. **That number is not a coverage gap**, and it is the specific
misreading this package exists to make checkable.

## Matching

Reporter names are matched exactly as the bulk table stores them. No
normalisation and no fuzzy matching, because a near-miss that silently
returned a plausible number would be worse than one that returns nothing.

Rows are read through `bulk.read`, so the count inherits the PostgreSQL
`COPY` dialect: `ESCAPE '\'` rather than doubled quotes. A default CSV reader
mis-splits rows containing quotes or embedded newlines, and the citations
table has both.

## Memory

Only clusters carrying one of the two reporters are held. For any real pair
that is tens of thousands of identifiers against eighteen million rows
scanned, so the pass is bounded by disk and decompression rather than memory.

## Stated limitations

Carried in every artifact, in `caveats`:

- Counts citations, not cases. A cluster carrying no citation in a reporter is
  not an absent case.
- The share of one reporter's clusters carrying no citation in the other is
  not a coverage gap.
- Exact over the generation named, not sampled. Superseded by any later drop.
- Reporter names matched exactly. A zero is more often a misspelling.
- Clusters are CourtListener's unit of decision. Duplicate records of one
  decision count more than once.

## Provenance

The artifact names the source file by sha256, taken from the bulk manifest, so
a figure can be recomputed against the same bytes rather than against whatever
the bucket holds later. It also records the tooling commit, whether that tree
was modified, and the row count declared by the manifest against the row count
actually scanned.

A count produced on a modified tree records `dirty: true`. Published figures
should come from a clean one.

## Tests

    py -m pytest parallel/tests -q

Hermetic. No network and no bulk data: the fixture is a small COPY-shaped CSV
carrying the escape and embedded-newline cases the dialect has to survive.
They cover the counting, the symmetry of the pair, the refusal to fuzzy match,
division by zero on an absent reporter, and that the artifact states a missing
citation is not a missing case.
