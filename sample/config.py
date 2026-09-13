"""Where drafts are written, and the filters that define each pool.

Drafts go outside every repository, including the private one they will
eventually live in. `register/` already owns that rule; this reuses it rather
than restating it, so there is one place to change if it ever needs changing.
"""

from __future__ import annotations

from pathlib import Path

from register.config import UnsafeLocation, check_no_repository  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = REPO_ROOT.parent

#: Drafts and pools. Not a repository, and the guard refuses it if it becomes
#: one.
DEFAULT_WORKDIR = WORKSPACE / "query-drafts"

#: Reporters excluded from metadata questions outright. A question about a
#: Supreme Court case tests recall, not retrieval, and these are famous by
#: construction.
EXCLUDED_REPORTERS = frozenset({
    "U.S.", "S. Ct.", "L. Ed.", "L. Ed. 2d", "U.S.L.W.", "Dall.", "Cranch",
    "Wheat.", "Pet.", "How.", "Black", "Wall.",
})

#: Reporters preferred for metadata questions: federal district and state
#: intermediate courts, where an unremarkable opinion is genuinely unremarkable.
PREFERRED_REPORTERS = frozenset({
    "F. Supp.", "F. Supp. 2d", "F. Supp. 3d",
    "F.R.D.", "B.R.",
    "Cal. Rptr.", "Cal. Rptr. 2d", "Cal. Rptr. 3d",
    "N.Y.S.", "N.Y.S.2d", "N.Y.S.3d",
    "A.2d", "A.3d", "N.E.2d", "N.E.3d", "N.W.2d", "P.2d", "P.3d",
    "S.E.2d", "S.W.2d", "S.W.3d", "So. 2d", "So. 3d",
})

#: Obscurity, operationalised. A case cited more than this is not unremarkable,
#: and the whole point of the preference is to test retrieval over recall.
MAX_CITATION_COUNT = 2

#: The age floor, which the citation count alone cannot supply. A low count
#: means two different things: a case nobody found worth citing, and a case
#: filed too recently for anyone to have cited it yet. Only the first is
#: obscurity. A case filed in or before this year has had time to be cited and
#: was not.
MAX_FILED_YEAR = 2020

#: Case names that are docket entries rather than case names. CourtListener's
#: case_name carries order text, filing dates and editor tags for a small
#: number of records, and a question about the author of an order has no
#: answer to record.
DOCKET_NAME_PATTERNS = (
    r"(?i)\b(?:s\.\s*ct\.|ica)\s*(?:order|s\.\s*d\.\s*o\.)",
    r"(?i)\border\s*,\s*filed\b",
    r"(?i)\bfiled\s+\d{1,2}/\d{1,2}/\d{2,4}",
    r"(?i)\bpublic\s+version\s*:",
    r"(?i)\bapplication\s+for\s+writ",
    r"\[[a-z]{2,4}\]",
    r"\d{1,2}/\d{1,2}/\d{2,4}",
)

#: Beyond this a case name is a caption fragment rather than a name, and a
#: query built on it asks about something the reader cannot identify.
MAX_CASE_NAME_LENGTH = 90

#: A real, citable opinion rather than a table entry or an unpublished order.
REQUIRED_PRECEDENTIAL_STATUS = "Published"

#: Category B pool filters, matching what the parenthetical scan established.
MIN_PARENTHETICAL_SCORE = 0.8


def workdir(path=None) -> Path:
    """The draft directory, checked and created."""
    target = Path(path or DEFAULT_WORKDIR).expanduser()
    checked = check_no_repository(target)
    checked.mkdir(parents=True, exist_ok=True)
    return checked
