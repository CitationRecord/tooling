"""CourtListener citation resolver for the Citation Record.

Resolves a case citation against CourtListener and reports one of three
outcomes: resolved, not found in CourtListener, or outside CourtListener's
coverage. The third is not a weaker version of the second. Treating a
coverage gap as a fabrication would inflate error rates against systems
drawing on broader corpora, so a coverage gap feeds the unscorable bucket
rather than the Class 1 count.
"""

__version__ = "0.1.0"

SCHEMA = "citationrecord.resolve.v1"

USER_AGENT = (
    f"CitationRecordResolver/{__version__} "
    "(+https://citationrecord.org; citation verification for the "
    "Citation Record benchmark)"
)

TOKEN_ENV_VAR = "COURTLISTENER_API_TOKEN"
