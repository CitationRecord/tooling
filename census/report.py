"""Statistics and output.

Two artifacts: a JSON file carrying everything measured, and a markdown table.
Both state their own limitations, because a number that travels without its
sample size eventually gets quoted without it.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from . import ARTIFACT_KIND, SCHEMA, __version__
from .store import ProbeState, iso_utc

#: The threshold the resolver uses to call a volume densely held, repeated
#: here so the probe reports against the same line the resolver draws.
SPARSE_BELOW = 50


@dataclass
class ReporterStats:
    reporter: str
    name: str
    category: str
    volumes_sampled: int
    volumes_measured: int
    volumes_errored: int
    opinion_counts: list = field(default_factory=list)
    median_opinions: float | None = None
    mean_opinions: float | None = None
    min_opinions: int | None = None
    max_opinions: int | None = None
    proportion_zero: float | None = None
    proportion_under_50: float | None = None
    reporter_present: int | None = None
    volumes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "reporter": self.reporter,
            "name": self.name,
            "category": self.category,
            "volumes_sampled": self.volumes_sampled,
            "volumes_measured": self.volumes_measured,
            "volumes_errored": self.volumes_errored,
            "opinion_counts": self.opinion_counts,
            "median_opinions": self.median_opinions,
            "mean_opinions": self.mean_opinions,
            "min_opinions": self.min_opinions,
            "max_opinions": self.max_opinions,
            "proportion_zero": self.proportion_zero,
            "proportion_under_50": self.proportion_under_50,
            "reporter_present_first_page": self.reporter_present,
            "volumes": self.volumes,
        }


def summarise(state: ProbeState) -> list:
    """Per-reporter arithmetic over the measured volumes."""
    frame = {entry["key"]: entry for entry in state.frame}
    by_reporter: dict = {}
    for result in state.results:
        by_reporter.setdefault(result["reporter"], []).append(result)

    stats = []
    for key, planned in state.plan.items():
        entry = frame.get(key, {})
        rows = sorted(by_reporter.get(key, []), key=lambda r: r["volume"])
        measured = [r for r in rows if not r.get("error")]
        counts = [r["opinion_count"] for r in measured]

        stat = ReporterStats(
            reporter=key,
            name=entry.get("name", key),
            category=entry.get("category", ""),
            volumes_sampled=len(planned),
            volumes_measured=len(measured),
            volumes_errored=len(rows) - len(measured),
            opinion_counts=counts,
            reporter_present=state.reporter_present.get(key),
            volumes=[
                {
                    "volume": r["volume"],
                    "stratum": r["stratum"],
                    "opinions": r["opinion_count"],
                    "page_low": r["page_low"],
                    "page_high": r["page_high"],
                    "pages_are_complete": r["pages_are_complete"],
                    "error": r.get("error"),
                }
                for r in rows
            ],
        )
        if counts:
            stat.median_opinions = float(statistics.median(counts))
            stat.mean_opinions = round(statistics.fmean(counts), 1)
            stat.min_opinions = min(counts)
            stat.max_opinions = max(counts)
            stat.proportion_zero = round(sum(1 for c in counts if c == 0) / len(counts), 3)
            stat.proportion_under_50 = round(
                sum(1 for c in counts if c < SPARSE_BELOW) / len(counts), 3
            )
        stats.append(stat)
    return stats


def limitations(state: ProbeState, stats: list) -> list:
    """Stated in the output itself, not only in a README."""
    per_reporter = state.per_reporter
    sampled = ", ".join(
        f"{s.reporter} n={s.volumes_measured}" for s in stats
    )
    return [
        f"THIS IS A SCOPE PROBE, NOT A CENSUS. {per_reporter} volumes were "
        "sampled per reporter. That is far too few to support a published "
        "coverage figure, and these numbers must not be quoted as section 4.2. "
        "The census runs against the bulk data drop.",
        f"Sample size per reporter: {sampled}.",
        "Opinion counts are a proxy for coverage, not a direct measure. They "
        "count the records CourtListener indexes against a volume, which is "
        "not the number of opinions the volume actually contains. A volume "
        "with a high count is well represented; a low count means only that "
        "few records reference it.",
        "A zero count means CourtListener indexes no citation in that "
        "reporter and volume. It does NOT establish that the underlying cases "
        "are absent. The same decisions may be held under a parallel "
        "reporter: a California case carrying no Cal. App. 5th citation may "
        "still be present with a Cal. Rptr. 3d one. This probe measures "
        "citation coverage per reporter, not case coverage, and cannot tell "
        "the two apart. Resolving that requires joining citations by cluster, "
        "which the bulk data supports and the API does not.",
        "A volume's page range is inferred from the citations on sampled "
        "records, not known. Where pages_are_complete is false the sample was "
        "truncated at one page of results, so the true low and high pages may "
        "lie outside the observed range.",
        "Volumes are drawn deterministically from the early, middle and recent "
        "thirds of each reporter's declared range, with the remainder weighted "
        "to the recent third. They are not a random sample, so these figures "
        "carry no confidence interval.",
        "Each reporter's volume range is a declared parameter recorded in the "
        "frame, not read from CourtListener. Deriving the range from "
        "CourtListener would hide the gaps this measures.",
        "Counts come from the structured citation filter, which matches the "
        "reporter field exactly. An earlier phrase-match approach conflated "
        "reporters sharing a name prefix and is not used here.",
        "Measurements are a snapshot. CourtListener ingests continuously, so "
        "every figure is valid only as of the timestamp recorded beside it.",
    ]


def build_document(state: ProbeState) -> dict:
    stats = summarise(state)
    return {
        "schema": SCHEMA,
        "artifact_kind": ARTIFACT_KIND,
        "not_publishable_as": "methodology section 4.2",
        "tool_version": __version__,
        "generated_at_utc": iso_utc(),
        "started_at_utc": state.started_at_utc,
        "updated_at_utc": state.updated_at_utc,
        "per_reporter_sample": state.per_reporter,
        "requests_used": state.requests_used,
        "provenance": state.provenance,
        "frame": state.frame,
        "plan": state.plan,
        "limitations": limitations(state, stats),
        "reporters": [s.as_dict() for s in stats],
    }


def _number(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _percent(value) -> str:
    return "-" if value is None else f"{round(value * 100)}%"


def to_markdown(document: dict) -> str:
    """A markdown table, headed by what it is not."""
    lines = [
        "# CourtListener reporter coverage — scope probe",
        "",
        f"**Scope probe, n={document['per_reporter_sample']} volumes per reporter. "
        "Not a census. Not publishable as methodology section 4.2.**",
        "",
        f"Generated {document['generated_at_utc']} using "
        f"{document['requests_used']} CourtListener requests.",
        "",
        "| Reporter | Category | Volumes | Median | Mean | Min | Max | Zero | Under 50 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for reporter in document["reporters"]:
        lines.append(
            "| {r} | {c} | {n} | {med} | {mean} | {lo} | {hi} | {z} | {u} |".format(
                r=reporter["reporter"],
                c=reporter["category"],
                n=reporter["volumes_measured"],
                med=_number(reporter["median_opinions"]),
                mean=_number(reporter["mean_opinions"]),
                lo=_number(reporter["min_opinions"]),
                hi=_number(reporter["max_opinions"]),
                z=_percent(reporter["proportion_zero"]),
                u=_percent(reporter["proportion_under_50"]),
            )
        )

    lines += ["", "## Volumes sampled", ""]
    for reporter in document["reporters"]:
        volumes = ", ".join(
            f"{v['volume']} ({v['stratum']}): "
            + (f"error" if v.get("error") else f"{v['opinions']}")
            for v in reporter["volumes"]
        )
        lines.append(f"- **{reporter['reporter']}** — {volumes or 'none measured'}")

    lines += ["", "## Stated limitations", ""]
    for item in document["limitations"]:
        lines.append(f"- {item}")

    provenance = document.get("provenance") or {}
    lines += [
        "",
        "## Provenance",
        "",
        f"- Tool: census {document['tool_version']}",
        f"- Tooling commit: {(provenance.get('tooling') or {}).get('commit')}"
        f" (dirty: {(provenance.get('tooling') or {}).get('dirty')})",
        f"- Methodology version: {provenance.get('methodology_version')}",
        f"- API: {provenance.get('api')}",
        f"- User agent: {provenance.get('user_agent')}",
        f"- Started {document['started_at_utc']}, last updated {document['updated_at_utc']}",
        "",
    ]
    return "\n".join(lines)


def write(document: dict, json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(to_markdown(document), encoding="utf-8")
