"""Which bulk files matter, in what order, and what lives in each.

Import order is a dependency order, not a preference. Courts come first
because nearly every other type points at them.

The opinions file is deliberately absent. At roughly 51 GiB it is 85% of the
whole drop and holds full opinion text, which a coverage census never reads.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from . import BUCKET_PREFIX, BUCKET_URL, USER_AGENT

S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


@dataclass(frozen=True)
class BulkFile:
    """One file in a generation, and why the census wants it."""

    key: str
    stem: str
    order: int
    purpose: str
    required: bool = True
    compressed: bool = True

    def object_key(self, generation: str) -> str:
        suffix = ".csv.bz2" if self.compressed else self.extension
        return f"{BUCKET_PREFIX}{self.stem}-{generation}{suffix}"

    @property
    def extension(self) -> str:
        return ".sql" if self.key == "schema" else ".sh"


#: Ordered by import dependency.
CATALOG: tuple = (
    BulkFile(
        key="schema", stem="schema", order=0, compressed=False,
        purpose="Table definitions generated alongside the drop. Read first: "
                "it settles which table carries reporter, volume and page.",
    ),
    BulkFile(
        key="load-script", stem="load-bulk-data", order=1, compressed=False,
        purpose="CourtListener's own loader. Documents the exact COPY options "
                "and the order they import in.",
    ),
    BulkFile(
        key="courts", stem="courts", order=2,
        purpose="Court identifiers and full names. Nearly every other type "
                "depends on this, so it imports first.",
    ),
    BulkFile(
        key="citations", stem="citations", order=3,
        purpose="Reporter, volume and page per cluster. The census counts "
                "these. Expected to be the table the API exposes as the "
                "nested citations array.",
    ),
    BulkFile(
        key="opinion-clusters", stem="opinion-clusters", order=4,
        purpose="Case name, date filed, precedential status and the docket "
                "link that reaches the court. Joined to citations for era "
                "and jurisdiction analysis.",
    ),
    BulkFile(
        key="citation-map", stem="citation-map", order=5,
        purpose="Opinion cites opinion, with depth. For sampling later. Not "
                "used for scoring.",
    ),
)

BY_KEY = {f.key: f for f in CATALOG}

#: Present in the drop but deliberately not fetched.
EXCLUDED = {
    "opinions": "roughly 51 GiB of full opinion text; a coverage census never "
                "reads opinion bodies",
    "dockets": "roughly 4.7 GiB; the census reaches courts through clusters",
}


def select(keys=None) -> list:
    """Catalog entries in import order, optionally filtered."""
    chosen = CATALOG if not keys else tuple(BY_KEY[k] for k in keys if k in BY_KEY)
    unknown = [k for k in (keys or ()) if k not in BY_KEY]
    if unknown:
        raise KeyError(f"unknown bulk file(s): {', '.join(unknown)}")
    return sorted(chosen, key=lambda f: f.order)


def list_generation(generation: str, session=None, timeout: float = 120.0) -> dict:
    """Every object in the bucket for one generation, keyed by object key.

    Read-only and unauthenticated: the bucket is public.
    """
    http = session or requests.Session()
    http.headers.setdefault("User-Agent", USER_AGENT)

    found: dict = {}
    token = None
    for _ in range(50):
        params = {"list-type": "2", "prefix": BUCKET_PREFIX, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        response = http.get(BUCKET_URL, params=params, timeout=timeout)
        response.raise_for_status()
        root = ET.fromstring(response.content)

        for contents in root.findall("s3:Contents", S3_NS):
            key = contents.findtext("s3:Key", namespaces=S3_NS) or ""
            if generation not in key:
                continue
            found[key] = {
                "key": key,
                "size": int(contents.findtext("s3:Size", namespaces=S3_NS) or 0),
                "last_modified": contents.findtext("s3:LastModified", namespaces=S3_NS),
                "etag": (contents.findtext("s3:ETag", namespaces=S3_NS) or "").strip('"'),
            }

        if (root.findtext("s3:IsTruncated", namespaces=S3_NS) or "").lower() != "true":
            break
        token = root.findtext("s3:NextContinuationToken", namespaces=S3_NS)
    return found


def plan(generation: str, keys=None, session=None) -> tuple:
    """Pair catalog entries with what the bucket actually holds."""
    available = list_generation(generation, session=session)
    entries = []
    missing = []
    for bulk_file in select(keys):
        object_key = bulk_file.object_key(generation)
        listing = available.get(object_key)
        if listing is None:
            missing.append(object_key)
            continue
        entries.append((bulk_file, listing))
    return entries, missing, available
