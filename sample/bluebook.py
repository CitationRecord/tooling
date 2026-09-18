"""Assembling the citation a Bluebook question actually asks for.

The citation question asks for *a Bluebook-formatted citation*. The recorded
answer used to be the bare reporter citation, `513 B.R. 896`. Those are not the
same thing, and the difference is not cosmetic: a Bluebook citation names the
case, the reporter, the deciding court and the year, and a system returning the
correct one would not have matched a ground truth holding a third of it.

**This is the authorship defect in a different question.** There the question
named something that did not exist; here the question asked for one thing and
the answer recorded another. Both produce an item whose correct response scores
as wrong, and both were invisible until somebody compared the question with the
answer rather than checking that the answer was populated.

The court abbreviation is not derived here. Bluebook Table T1 is long, and a
hand-written mapping would be a second source of truth that drifts from the
first. CourtListener's `courts` endpoint carries `citation_string`, which is
the court's Bluebook abbreviation as that corpus records it -- `Bankr. W.D.
Mich.`, `Fla. Dist. Ct. App.`, `Tex.` -- so it is fetched with everything else
and recorded with its provenance.

No model is in the loop. This joins four strings in a documented order.
"""

from __future__ import annotations

#: The form, named in the artifact so a third party can check the assembly
#: without reading this source.
FORM = ("Bluebook rule 10: case name, comma, reporter citation, then the "
        "deciding court and the year in parentheses. The court is omitted "
        "only where the reporter alone identifies it, which is never true of "
        "the reporters this category draws from.")

#: Where the court abbreviation comes from, recorded on every answer built.
COURT_SOURCE = ("CourtListener courts endpoint, citation_string field; the "
                "court's Bluebook abbreviation as that corpus records it, "
                "not a mapping maintained here")


class Incomplete(ValueError):
    """A component is missing, so no citation is built rather than a partial one."""


def citation(case_name: str, reporter_citation: str, court_abbreviation: str,
             year) -> str:
    """The complete Bluebook citation, or a refusal.

    Refuses on any missing component. A citation assembled around a blank is
    worse than no citation: it looks like an answer, and the item would ship
    with ground truth that no correct response can match.
    """
    missing = [name for name, value in (
        ("case name", case_name),
        ("reporter citation", reporter_citation),
        ("court abbreviation", court_abbreviation),
        ("year", year),
    ) if not str(value or "").strip()]
    if missing:
        raise Incomplete(
            f"cannot build a Bluebook citation without {', '.join(missing)}")

    name = str(case_name).strip().rstrip(",")
    return (f"{name}, {str(reporter_citation).strip()} "
            f"({str(court_abbreviation).strip()} {year})")


def answer(case_name: str, reporter_citation: str, court_abbreviation: str,
           year, **extra) -> dict:
    """The answer, its parts, and how it was assembled.

    The parts travel beside the whole because the pilot showed three systems
    returning three different citation strings for one case, two of them
    carrying a parallel citation the ground truth does not hold. A scorer
    checking a response against one string would have to reject a correct
    parallel form; a scorer holding the components can check them one at a
    time and say which part differs.
    """
    full = citation(case_name, reporter_citation, court_abbreviation, year)
    return {
        "answer": full,
        "answer_components": {
            "case_name": str(case_name).strip().rstrip(","),
            "reporter_citation": str(reporter_citation).strip(),
            "court_abbreviation": str(court_abbreviation).strip(),
            "year": year,
        },
        "answer_form": FORM,
        "court_abbreviation_source": COURT_SOURCE,
        "matching_note": (
            "A response carrying a parallel citation, or the official reporter "
            "alongside the regional one, is not thereby wrong. The components "
            "are recorded so a scorer can check the case name, the reporter "
            "citation, the court and the year separately rather than compare "
            "one string to another."
        ),
        **extra,
    }
