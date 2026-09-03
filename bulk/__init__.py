"""CourtListener bulk data loader for the Citation Record.

Replaces API probing with exact local measurement. The API can say how many
opinions it indexes against a volume; it cannot group citations by case across
reporters, so it cannot tell a coverage gap from a case held under a parallel
reporter. That ambiguity is what misclassified Mata v. Avianca, and the bulk
tables resolve it.

Snapshots, not deltas. Every census result states which generation it was
computed from.

No token. The bucket is public. No model is in the loop.
"""

__version__ = "0.1.0"

SCHEMA = "citationrecord.bulk.v1"

USER_AGENT = (
    f"CitationRecordBulk/{__version__} "
    "(+https://citationrecord.org; bulk data loader for the "
    "Citation Record benchmark)"
)

BUCKET_URL = "https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/"
BUCKET_PREFIX = "bulk-data/"

#: Generations are produced on the last day of March, June, September and
#: December. This is the most recent completed drop.
DEFAULT_GENERATION = "2026-06-30"
