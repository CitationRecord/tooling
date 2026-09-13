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

**Deletion-only is necessary and not sufficient.** Three ways a mechanically
valid deletion still fails:

*The clause may carry an inference.* "Statements made as a union representative
are not part of official police duties and thus are afforded First Amendment
protection" deletes to "...are part of official duties and thus are afforded
protection". The conclusion followed from the negated element, so removing the
negation does not invert the claim, it breaks it. What comes out is incoherent
rather than contrary, and no court could hold it.

*The clause may not stand alone.* "Their different procedural requirements do
not render FLSA and state wage law class actions incompatible" inverts cleanly,
but "their" refers to something in the surrounding opinion. Lifted into a query
it refers to nothing, the proposition is not fully stated, and a system cannot
properly be right or wrong about it.

*The negator may govern a condition rather than the claim.* "ERISA applied when
the employer could not carry out its obligations with an unthinking, one-time
application" deletes to a sentence that still says ERISA applied, under the
opposite condition. The case held the first; it does not hold the opposite of
the second. That is a different proposition rather than a contrary one, and
ground truth for this category is supposed to be the case holding the opposite.

Judging that a conclusion depends on its premise, that a pronoun has no
antecedent in its own sentence, or that a negator sits inside a subordinate
clause, is grammar rather than doctrine. None of it requires knowing any law.

**The comma condition on the subordinate-clause rule is a heuristic rather than
a parse.** It treats a subordinator set off by a comma as an aside and one that
is not as opening a clause, which keeps "a defendant's true, if misleading,
testimony cannot support a conviction" and rejects "ERISA applied when the
employer could not". That distinction holds on the draws examined so far and is
not a grammatical guarantee. It is better to say so here than to have the next
person discover it.

**How these rules were derived, which matters as much as what they are.** All
three came from reading draws, not from reasoning about negation in advance.
Deletion-only was designed first and looked sufficient; each insufficiency was
found by looking at what it actually produced. That is evidence the rule set is
incomplete rather than finished, and a fourth failure shape probably sits in the
pool waiting for a draw to surface it. The rules are empirical, built from
observed failures, and not proven exhaustive. Every artifact they touch says so.

Every rule is named, and the name is recorded on the item it produced, so the
transformation can be checked rather than trusted. Anything the rules cannot
handle is rejected: the candidate pool is large enough that discarding costs
nothing, and a doubtful negation costs a great deal.

No model is in the loop. This deletes the word "not".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Carried into every query-set artifact. Not a caveat appended to the rules:
#: a statement of where they came from, which bears on how far to trust them.
RULE_PROVENANCE = {
    "derivation": "empirical",
    "how": (
        "Every rejection rule beyond deletion-only was derived from reading a "
        "draw and finding a negation that was mechanically valid and still "
        "wrong. None was anticipated in advance."
    ),
    "exhaustive": False,
    "implication": (
        "Three insufficiencies in deletion-only were found by looking at three "
        "draws. The rule set should be read as incomplete rather than "
        "finished, and a further failure shape probably exists in the pool."
    ),
    "heuristics": [
        "The comma condition on the subordinate-clause rule is a heuristic "
        "rather than a parse: a subordinator set off by a comma is treated as "
        "an aside, one that is not as opening a clause. It holds on the draws "
        "examined so far and is not a grammatical guarantee."
    ],
}

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

#: Any negator, for locating where in the sentence the negation sits.
ANY_NEGATOR = re.compile(rf"\b(?:{_AUX})\s+not\b|\bcannot\b", re.IGNORECASE)

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

#: Words marking a conclusion drawn from what precedes them. Where the
#: conclusion rests on the negated element, deleting the negation breaks the
#: inference instead of inverting the claim.
INFERENTIAL = re.compile(
    r"\b(thus|therefore|accordingly|hence|consequently|ergo|"
    r"and\s+so|as\s+a\s+result|for\s+that\s+reason|it\s+follows)\b",
    re.IGNORECASE,
)

#: A subordinating conjunction opening a clause. The negative lookbehind is the
#: heuristic named in the module docstring: a subordinator preceded by a comma
#: is treated as an aside rather than as opening a clause.
SUBORDINATOR = re.compile(
    r"(?<!,)\s\b(when|where|if|because|unless|although|though|while|since|"
    r"whenever|wherever|provided)\b",
    re.IGNORECASE,
)

#: Pronouns and determiners whose antecedent lives outside the parenthetical.
#: A clause opening with one of these does not state its own subject.
UNBOUND_OPENERS = frozenset({
    "their", "theirs", "its", "his", "her", "hers", "this", "that", "these",
    "those", "such", "it", "they", "he", "she", "him", "them", "said",
})

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

_FIRST_WORD = re.compile(r"[A-Za-z']+")

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


def opens_unbound(body: str) -> bool:
    """Whether the clause begins with a pronoun that names nothing in it."""
    match = _FIRST_WORD.search(body or "")
    return bool(match) and match.group().lower() in UNBOUND_OPENERS


def negator_governs_a_condition(text: str) -> bool:
    """Whether the negator sits inside a subordinate clause.

    If it does, deleting it flips the circumstance rather than the claim, and
    the source case does not hold the opposite of what comes out.
    """
    negator = ANY_NEGATOR.search(text or "")
    if negator is None:
        return False
    return any(match.start() < negator.start()
               for match in SUBORDINATOR.finditer(text))


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
    if INFERENTIAL.search(text):
        return "carries an inference; deleting the negator breaks it"
    if negator_governs_a_condition(text):
        return "negator governs a condition, not the claim"

    body = strip_prefix(text)
    if opens_unbound(body):
        return "opens with an unbound pronoun; does not stand alone"

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
