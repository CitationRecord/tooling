"""Recording ground truth for a drawn query set.

The one stage that touches the network, and the only one that writes records
that cannot be taken back. Kept separate for exactly that reason: the draw is
reviewed first, and nothing here runs against a selection nobody has read.

Every answer is resolved through `resolve/`, so each item leaves a
`citationrecord.resolve.v1` record in the lookup journal rather than an
assertion in this file. Where an answer needs a field the resolver does not
carry, the raw request is named in the item's own provenance with its endpoint
and timestamp, so a reader can tell a journalled lookup from a bare fetch.

Authorship is the only answer taken from an endpoint rather than a citation
lookup. The clusters `judges` column is a free-text panel listing, not an
attribution, and deriving an answer from data that does not carry it is how an
unsupportable figure reached print once already.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Opinion types CourtListener uses for the opinion that carries the judgment.
#: Anything else is a concurrence, a dissent or an addendum, none of which is
#: the majority.
MAJORITY_TYPES = ("010combined", "020lead", "015unamimous", "025plurality")

#: Courts that sit as one judge, matched on the resolved court name.
#:
#: The second half of the authorship guard. `sample/config.py` rejects the
#: reporters that are single-judge by construction, at draw time, from pool
#: fields. That check cannot see the court, and the regional reporters carry
#: some trial-court decisions, so this catches what the reporter did not say.
#:
#: Refusing here leaves the item incomplete rather than answered, which is the
#: behaviour this module already has for every answer it cannot establish. An
#: incomplete item is visible in the artifact and blocks registration; a wrong
#: one is invisible and gets published.
SINGLE_JUDGE_COURTS = (
    "district court",
    "bankruptcy",
    "court of claims",           # the pre-1982 single-judge trial court
    "tax court",
    "superior court",            # state trial courts in most states
    "circuit court",             # state trial courts, e.g. Illinois
    "court of common pleas",
    "surrogate",
    "family court",
    "municipal court",
    "justice court",
    "probate",
)


def authorship_is_undefined(court: str, opinion: dict) -> str | None:
    """Why the majority author cannot be recorded for this opinion.

    Checked against what the API actually returned, rather than inferred from
    the reporter, so it catches the trial-court decisions that reach a
    regional reporter.
    """
    name = (court or "").lower()
    for marker in SINGLE_JUDGE_COURTS:
        if marker in name:
            return (f"{court} sits as a single judge, so there is no majority "
                    f"opinion to attribute")
    if opinion.get("per_curiam"):
        return "the opinion is per curiam, so it is by the court, not a judge"
    return None


@dataclass
class Plan:
    """What resolve will ask for, before it asks."""

    steps: list = field(default_factory=list)

    def add(self, query_id: str, kind: str, requests: list,
            journal_records: int, writes: str) -> None:
        self.steps.append({
            "query_id": query_id,
            "kind": kind,
            "requests": requests,
            "journal_records": journal_records,
            "writes": writes,
        })

    @property
    def journal_records(self) -> int:
        return sum(s["journal_records"] for s in self.steps)

    @property
    def request_count(self) -> int:
        return sum(len(s["requests"]) for s in self.steps)


def plan_for(document: dict) -> Plan:
    """Exactly what each item will request, and what it will write.

    Request counts are upper bounds where the resolver may make a second call.
    A citation lookup costs one request, plus a docket fetch to reach the court,
    plus a court fetch the first time that court is seen. Courts are cached
    permanently, so repeats within a run cost nothing.
    """
    plan = Plan()
    for query in document["queries"]:
        if query["category"] == "metadata":
            kind = query["metadata_kind"]
            cite = query["subject"]["citation"]
            requests = [
                f"citation-lookup: {cite}",
                "dockets/{docket_id} to reach the court",
                "courts/{court_id} unless already cached",
            ]
            writes = ("cluster id, case name, court, year, precedential status; "
                      "one resolve.v1 record")
            if kind == "author":
                requests.append(
                    "opinions?cluster={cluster_id} for the majority author")
                writes = ("author of the majority opinion, plus the above; "
                          "one resolve.v1 record and one un-journalled fetch")
            plan.add(query["id"], f"metadata:{kind}", requests, 1, writes)
        else:
            opinion = query["subject"]["described_opinion_id"]
            plan.add(
                query["id"], "negated-parenthetical",
                [
                    f"opinions/{opinion} to reach its cluster",
                    "clusters/{cluster_id} for the citation and case name",
                    "citation-lookup on that citation",
                    "dockets/{docket_id} to reach the jurisdiction",
                    "courts/{court_id} unless already cached",
                ],
                1,
                ("jurisdiction, case name, citation, year; the original "
                 "parenthetical stays the recorded holding; one resolve.v1 "
                 "record"),
            )
    return plan


def majority_author(client, cluster_id, court=None) -> dict:
    """The author of the opinion carrying the judgment, or a stated absence.

    Returns what was found and how, never a guess. An opinion with no recorded
    author yields None rather than a name derived from somewhere else, and the
    item is left incomplete rather than answered wrongly.
    """
    response = client._request(
        "GET", "opinions",
        params={"cluster": str(cluster_id),
                "fields": "id,type,author_str,author,per_curiam,page_count"})
    results = response.json().get("results") or []

    ranked = sorted(
        results,
        key=lambda o: (MAJORITY_TYPES.index(o.get("type"))
                       if o.get("type") in MAJORITY_TYPES else 99))
    for opinion in ranked:
        if opinion.get("type") not in MAJORITY_TYPES:
            continue
        undefined = authorship_is_undefined(court, opinion)
        if undefined:
            return {"author": None, "opinion_id": opinion.get("id"),
                    "opinion_type": opinion.get("type"),
                    "undefined": undefined,
                    "source": "refused: authorship is not well defined"}
        name = (opinion.get("author_str") or "").strip()
        if name:
            return {"author": name, "opinion_id": opinion.get("id"),
                    "opinion_type": opinion.get("type"),
                    "source": "opinions endpoint, author_str"}
        if opinion.get("author"):
            return {"author": None, "opinion_id": opinion.get("id"),
                    "opinion_type": opinion.get("type"),
                    "author_ref": opinion.get("author"),
                    "source": "opinions endpoint, author reference only"}
    return {"author": None, "opinion_id": None, "opinion_type": None,
            "source": "opinions endpoint returned no majority with an author"}
