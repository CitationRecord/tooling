"""Parallel reporter coverage, counted per cluster rather than per reporter.

A reporter-by-reporter reading of the bulk citations table overstates missing
coverage wherever a case is held under a parallel citation. California is the
clearest instance: an opinion may carry a Cal. Rptr. 3d citation, a Cal. App.
5th citation, or both, and counting one reporter alone cannot tell an absent
case from a case indexed under the other name.

This package counts clusters, not citations. For a pair of reporters it
reports how many clusters carry one, how many carry the other, and how many
carry both, so the per-reporter reading and the per-cluster reading can be
compared directly instead of one standing in for the other.

It answers a narrower question than it may appear to. A cluster carrying no
citation in a reporter is not an absent case, and nothing here establishes
that any case is missing from CourtListener. It measures how citations are
distributed across reporters, and nothing else.

No model is in the loop. This counts rows and reports arithmetic.
"""

__version__ = "0.1.0"

SCHEMA = "citationrecord.parallel.v1"

#: Stamped into every output. Unlike the census scope probe this is an exact
#: count over a complete bulk generation and carries no sampling caveat. It is
#: still a count of citations, not of cases.
ARTIFACT_KIND = "exact_count"
