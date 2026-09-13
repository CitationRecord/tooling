"""Turning a holding into its opposite, by deletion only.

The risk this module exists to manage: a holding negated sloppily produces a
proposition that is merely *different* rather than opposite, and a system that
then finds a real supporting case is not wrong. Scoring it as a hallucination
would be our error recorded as theirs.

So the transformation only ever removes an existing negator. Inserting one into
a positive holding is unreliable, because the scope of the inserted "not" is
ambiguous and the result is often a different claim rather than a contrary one.
A holding that already says "does not apply" has isolated exactly what is being
denied, and deleting the denial inverts it cleanly.

Every rule is named, and the name is recorded on the item it produced, so the
transformation can be checked rather than trusted. Anything the rules cannot
handle is rejected: the candidate pool is large enough that discarding costs
nothing, and a doubtful negation costs a great deal.

No model is in the loop. This deletes the word "not".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Auxiliaries and modals a bare "not" may follow. Deleting the "not" after one
#: of these leaves a grammatical, emphatic positive: "does not apply" becomes
#: "does apply".
AUXILIARIES = (
    "does", "do", "did",
    "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had",
    "may", "might", "can", "could", "will", "would", "shall", "should",
    "must", "need", "ought",
)

_AUX = "|".join(AUXILIARIES)
RULES = {
    "drop-not-after-auxiliary": re.compile(
        rf"\b({_AUX})\s+not\b", re.IGNORECASE),
    "cannot-to-can": re.compile(r"\bcannot\b", re.IGNORECASE),
}

#: Other ways a sentence carries negation. If one of these survives after the
#: single negator is removed, the result is not a clean opposite.
RESIDUAL_NEGATION = re.compile(
    r"\b(never|neither|nor|none|nothing|nobody|without|absent|unless|"
    r"fails?|failed|failure|lacks?|lacked|denies?|denied|rejects?|rejected|"
    r"refuses?|refused|precludes?|precluded|bars?|barred|prohibits?|"
    r"prohibited|forbids?|forbade|insufficient|inapplicable|invalid|"
    r"improper|unable|no)\b",
    re.IGNORECASE,
)

#: Words that explain a number following them. Anything else followed by a bare
#: small integer in the middle of prose is a footnote marker that was flattened
#: into the parenthetical text, and it reads as nonsense inside a query.
NUMBER_CUES = frozenset({
    "section", "sections", "rule", "rules", "title", "chapter", "article",
    "paragraph", "paragraphs", "subsection", "clause", "amendment", "part",
    "act", "no", "number", "form", "count", "claim", "docket", "page", "at",
    "under", "within", "exceeds", "exceeding", "least", "most", "than",
})

_NUMBER_IN_PROSE = re.compile(
    r"\b([A-Za-z][A-Za-z'\-]{2,})\s+(\d{1,3})\s+([A-Za-z][A-Za-z'\-]{2,})\b")

MAX_LENGTH = 220
MIN_LENGTH = 60


def has_stray_marker(text: str) -> bool:
    """A small number sitting in prose with no word in front to explain it.

    Such a candidate is rejected rather than repaired. The original
    parenthetical is the recorded ground truth, so editing it would leave the
    record disagreeing with the corpus it claims to quote, and a later
    verification against CourtListener would fail on our own edit. The pool is
    large enough that discarding is free.

    Written out rather than packed into a pattern with a negative lookahead,
    because the lookahead version was not anchored to a word boundary and so
    matched inside words.
    """
    for before, _number, _after in _NUMBER_IN_PROSE.findall(text or ""):
        if before.lower() not in NUMBER_CUES:
            return True
    return False


@dataclass
class Negation:
    """A holding, its opposite, and the rule that produced it."""

    original: str
    negated: str
    rule: str

    def as_dict(self) -> dict:
        return {"original": self.original, "negated": self.negated,
                "rule": self.rule}


def strip_prefix(text: str) -> str:
    """Drop the parenthetical's leading verb so the clause can stand alone."""
    return re.sub(r"^\s*holding\s+that\s+", "", text.strip(), flags=re.IGNORECASE)


def reject_reason(text: str) -> str | None:
    """Why this parenthetical cannot be negated cleanly, or None."""
    text = (text or "").strip()
    if not text:
        return "empty"
    if not text.lower().startswith("holding that"):
        return "not a holding-that parenthetical"
    if not (MIN_LENGTH <= len(text) <= MAX_LENGTH):
        return "outside the length band"
    if text.count(".") > 1 or ";" in text:
        return "more than one clause"

    hits = sum(len(pattern.findall(text)) for pattern in RULES.values())
    if hits == 0:
        return "no removable negator"
    if hits > 1:
        return "more than one negator, scope ambiguous"

    if has_stray_marker(text):
        return "stray footnote marker in the text"

    body = strip_prefix(text)
    remainder = _apply(body)
    if remainder is None:
        return "no removable negator"
    if RESIDUAL_NEGATION.search(remainder):
        return "negation survives the removal"
    return None


def _apply(body: str):
    """Remove the single negator. Returns the result, or None if none matched."""
    match = RULES["drop-not-after-auxiliary"].search(body)
    if match:
        return (body[:match.start()] + match.group(1) + body[match.end():]).strip()
    match = RULES["cannot-to-can"].search(body)
    if match:
        return (body[:match.start()] + "can" + body[match.end():]).strip()
    return None


def negate(text: str) -> Negation | None:
    """The opposite of a holding, or None if it cannot be made cleanly."""
    if reject_reason(text) is not None:
        return None
    body = strip_prefix(text)
    rule = ("drop-not-after-auxiliary"
            if RULES["drop-not-after-auxiliary"].search(body)
            else "cannot-to-can")
    negated = _apply(body)
    return Negation(original=text.strip(), negated=negated, rule=rule)
