"""Public document archiver for the Citation Record.

Snapshots, hashes, and logs public documents whose publisher can change them:
vendor claims for the system profiles in methodology section 7, and the court
rules that ground-truth local-rules questions.

Those are the same case rather than one being an exception to the other. A
vendor edits its marketing page; a district court amends its local rules.
Either way a quotation taken today has to remain quotable months later, and
holding a publisher to its own words means having kept a copy and recorded
when it was taken.

Records are append-only; snapshots are written once and never edited.
"""

__version__ = "0.1.0"

SCHEMA = "citationrecord.archive.v1"

# Truthful identification. This string is sent on every request the archiver
# makes and is the token robots.txt rules are matched against. Design
# constraint: nothing misrepresents itself.
USER_AGENT = (
    f"CitationRecordArchiver/{__version__} "
    "(+https://citationrecord.org; archives public vendor claims "
    "for the Citation Record benchmark)"
)
