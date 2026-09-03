"""Reporter coverage measurement for the Citation Record.

Measures how densely CourtListener holds each reporter, volume by volume.

This package currently ships a SCOPE PROBE, not a census. The probe samples
four volumes per reporter to answer one question ahead of the full census:
whether district court and state citations are viable ground truth at all.
Four volumes cannot support a published coverage figure. The full census runs
against the bulk data drop and is what feeds methodology section 4.2.

No model is in the loop. This counts records and reports arithmetic.
"""

__version__ = "0.1.0"

SCHEMA = "citationrecord.census.probe.v1"

#: Stamped into every output so a probe result cannot be mistaken for the
#: census it precedes.
ARTIFACT_KIND = "scope_probe"
