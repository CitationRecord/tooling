# `coverage/` — reporter coverage measurement

Measures how densely CourtListener holds each reporter, volume by volume.

**What ships here today is a scope probe, not a census.** It samples four
volumes per reporter to answer one question ahead of the full census: whether
district court and state citations are viable ground truth at all. Four
volumes cannot support a published coverage figure. The full census runs
against the bulk data drop and is what feeds methodology section 4.2.

Every artifact this produces says so in its own output. The JSON carries
`artifact_kind: scope_probe` and `not_publishable_as: methodology section
4.2`; the markdown says it above the table, before any number; the state file
carries the same warning. Tests assert all three, because a number that
travels without its sample size eventually gets quoted without it.

No model is in the loop. This counts records and reports arithmetic.

## Use

```powershell
py -m coverage plan       # what would be measured, and what it costs
py -m coverage run        # measure; rerun to continue where it stopped
py -m coverage status     # how far along
py -m coverage report     # rewrite outputs from saved state
```

`--per-reporter N` changes the sample size, `--budget N` stops a session after
roughly N requests, `--out DIR` moves the output.

Exit codes: 0 complete, 1 an error, 3 stopped early with work remaining.

## What it measures

For each sampled volume, one structured query returns how many opinions
CourtListener indexes against that volume and the citations on the first page
of results, from which the observed page range is taken.

Per reporter the output reports volumes sampled, median and mean opinions per
volume, the minimum and maximum, the proportion of sampled volumes holding
zero opinions, and the proportion holding fewer than fifty. Fifty is the same
line `resolve/` uses to call a volume densely held, so the two components
report against the same threshold.

## Sampling

Volumes are drawn from the early, middle and recent thirds of each reporter's
range rather than uniformly, because ingestion quality varies by decade. With
four draws the remainder goes to the recent third, where district and state
coverage actually varies.

Sampling is **deterministic**. A rerun hits the same volumes, so there is no
seed to record and no way for a rerun to land on friendlier books. The exact
volumes measured are written into the output.

Each reporter's volume range is a **declared parameter**, recorded in the
frame with its source. It is not read from CourtListener. Asking CourtListener
where its volumes stop and then sampling only that range would hide exactly
the gaps this exists to find: a reporter running to volume 300 that
CourtListener carries to volume 90 would look complete.

## Exact counts, not phrase matches

Counts come from the structured citation filter, keyed on the reporter field.

An earlier approach queried the citation text as a phrase, which is a prefix
match: `citation:("347 U.S.")` also matches `347 U.S. App. D.C.`, returning
734 where the exact filter returns 691. Any reporter whose name prefixes
another was inflated. `resolve/` was fixed to use the same structured filter,
so both components now measure the same thing the same way.

## Rate limits and resuming

The binding constraint is 50 requests per hour on the user scope, with 125 per
day behind it. The probe writes its state to disk after **every** volume. A
run stopped by a rate limit, a reboot, or a day's wait resumes from the next
unmeasured volume. At fifty requests an hour, restarting from zero is not an
inconvenience, it is a lost day.

A rate limit ends a run cleanly with exit code 3 rather than failing it. What
was measured stays measured. Rerunning the same command continues.

The state file refuses to be reused by a different plan. Changing
`--per-reporter` against saved state is an error rather than a silent mix of
two sample sizes.

## Cost

Four reporters at four volumes each is sixteen volumes, costing twenty
requests best case and thirty-six worst case, since a volume needs a second
request only when its results exceed one page. That is well inside the eighty
request budget and under an hour of quota.

## Stated limitations

Carried in the output itself, not only here:

- The sample size per reporter, named explicitly.
- Opinion counts are a **proxy** for coverage, not a direct measure. They
  count records CourtListener indexes against a volume, which is not the
  number of opinions the volume contains.
- A volume's page range is **inferred** from citations on sampled records, not
  known. Where `pages_are_complete` is false the sample was truncated at one
  page, so the true edges may lie outside the observed range.
- Draws are deterministic and stratified, not random, so the figures carry no
  confidence interval.
- Volume ranges are declared parameters, not measurements.
- Every figure is a snapshot, valid as of the timestamp beside it.

## Provenance

Every run records the resolver and tool versions, the tooling commit and dirty
flag, the methodology version, the API, the user agent, the exact volumes
sampled, the request count, and start and update timestamps. It reuses the
client, throttle and cache from `resolve/`, so the token comes from the
environment and never appears in output.

## Tests

```powershell
py -m pytest coverage/tests
```

Hermetic. They cover the probe labelling in all three artifacts, deterministic
and stratified sampling, resumption after a rate limit fetching only what is
left, refusal to reuse state across a changed plan, the statistics, and the
presence of the three required limitations.
