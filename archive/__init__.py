"""Claim archiver for the Citation Record.

Snapshots, hashes, and logs vendor claims for the system profiles in
methodology section 7. Records are append-only; snapshots are written once
and never edited.
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
