"""Load and validate the YAML claim list."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
WAIT_STATES = {"load", "domcontentloaded", "networkidle", "commit"}


class ClaimsError(ValueError):
    """The claim list is malformed. Nothing is captured until it is fixed."""


@dataclass(frozen=True)
class Claim:
    """One tracked vendor claim.

    `verified` records whether the URL has been fetched and confirmed to be
    the primary source of the claim. It is required on every entry and has no
    default: an omitted flag is an error, never silent permission to fetch. A
    claim reconstructed from advertising or secondhand coverage carries
    `verified: false` and is never fetched, because a wrong source in the
    evidence archive is worse than a missing one.
    """

    id: str
    url: str | None
    claim_text: str
    verified: bool
    system: str | None = None
    vendor: str | None = None
    profile_ref: str | None = None
    notes: str | None = None
    wait_until: str | None = None
    settle_ms: int | None = None
    full_page: bool | None = None

    @property
    def origin(self) -> str:
        if not self.url:
            return ""
        parts = urlparse(self.url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def capturable(self) -> bool:
        """Whether a capture run may fetch this entry at all."""
        return self.verified and bool(self.url)

    @property
    def claim_text_sha256(self) -> str:
        return hashlib.sha256(self.claim_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClaimSet:
    """A parsed claim file plus the provenance needed to cite it."""

    claims: tuple[Claim, ...]
    path: Path
    file_sha256: str
    methodology_version: str | None = None
    edition: str | None = None

    def select(self, only: tuple[str, ...]) -> tuple[Claim, ...]:
        if not only:
            return self.claims
        known = {c.id for c in self.claims}
        missing = [i for i in only if i not in known]
        if missing:
            raise ClaimsError(f"no such claim id: {', '.join(sorted(missing))}")
        wanted = set(only)
        return tuple(c for c in self.claims if c.id in wanted)


def _text(raw: object, where: str, field: str, required: bool = False) -> str | None:
    value = raw
    if value is None:
        if required:
            raise ClaimsError(f"{where}: '{field}' is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ClaimsError(f"{where}: '{field}' must be a non-empty string")
    return value.strip()


def _parse_claim(raw: object, index: int) -> Claim:
    where = f"claims[{index}]"
    if not isinstance(raw, dict):
        raise ClaimsError(f"{where}: expected a mapping, got {type(raw).__name__}")

    claim_id = _text(raw.get("id"), where, "id", required=True)
    if not ID_RE.match(claim_id):
        raise ClaimsError(
            f"{where}: id {claim_id!r} must be 1-64 chars of letters, digits, "
            "dot, dash or underscore (it becomes a directory name)"
        )
    where = f"claims[{index}] ({claim_id})"

    # Required, and no default. Defaulting this to true would make a
    # forgotten key indistinguishable from a confirmed source.
    verified = raw.get("verified")
    if not isinstance(verified, bool):
        raise ClaimsError(
            f"{where}: 'verified' is required and must be true or false. "
            "Use true only if the URL has been fetched and confirmed to be the "
            "primary source of the claim; unconfirmed entries are never captured"
        )

    # A confirmed entry must say where the claim lives. An unconfirmed one may
    # not have a URL yet, which is exactly why it is not fetched.
    url = _text(raw.get("url"), where, "url", required=verified)
    if url is not None:
        parts = urlparse(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ClaimsError(f"{where}: url must be an absolute http(s) URL, got {url!r}")

    # The claim text is the point of the record. A snapshot without the
    # specific assertion being tracked is not evidence of anything.
    claim_text = _text(raw.get("claim"), where, "claim", required=True)

    wait_until = _text(raw.get("wait_until"), where, "wait_until")
    if wait_until is not None and wait_until not in WAIT_STATES:
        raise ClaimsError(
            f"{where}: wait_until must be one of {sorted(WAIT_STATES)}, got {wait_until!r}"
        )

    settle_ms = raw.get("settle_ms")
    if settle_ms is not None and (not isinstance(settle_ms, int) or settle_ms < 0):
        raise ClaimsError(f"{where}: settle_ms must be a non-negative integer")

    full_page = raw.get("full_page")
    if full_page is not None and not isinstance(full_page, bool):
        raise ClaimsError(f"{where}: full_page must be true or false")

    unknown = set(raw) - {
        "id", "url", "claim", "verified", "system", "vendor", "profile_ref",
        "notes", "wait_until", "settle_ms", "full_page",
    }
    if unknown:
        raise ClaimsError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")

    return Claim(
        id=claim_id,
        url=url,
        claim_text=claim_text,
        verified=verified,
        system=_text(raw.get("system"), where, "system"),
        vendor=_text(raw.get("vendor"), where, "vendor"),
        profile_ref=_text(raw.get("profile_ref"), where, "profile_ref"),
        notes=_text(raw.get("notes"), where, "notes"),
        wait_until=wait_until,
        settle_ms=settle_ms,
        full_page=full_page,
    )


def load_claims(path: Path) -> ClaimSet:
    """Parse and validate a claim file. Raises ClaimsError on any problem."""
    path = Path(path)
    if not path.is_file():
        raise ClaimsError(f"claim file not found: {path}")

    raw_bytes = path.read_bytes()
    try:
        doc = yaml.safe_load(raw_bytes.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ClaimsError(f"{path}: could not parse YAML: {exc}") from exc

    if not isinstance(doc, dict):
        raise ClaimsError(f"{path}: top level must be a mapping with a 'claims' key")

    version = doc.get("version", 1)
    if version != 1:
        raise ClaimsError(f"{path}: unsupported claim file version {version!r}")

    entries = doc.get("claims")
    if not isinstance(entries, list) or not entries:
        raise ClaimsError(f"{path}: 'claims' must be a non-empty list")

    claims = tuple(_parse_claim(raw, i) for i, raw in enumerate(entries))

    seen: dict[str, int] = {}
    for i, claim in enumerate(claims):
        if claim.id in seen:
            raise ClaimsError(
                f"{path}: duplicate claim id {claim.id!r} at claims[{seen[claim.id]}] "
                f"and claims[{i}]"
            )
        seen[claim.id] = i

    return ClaimSet(
        claims=claims,
        path=path,
        file_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        methodology_version=_text(doc.get("methodology_version"), str(path), "methodology_version"),
        edition=_text(doc.get("edition"), str(path), "edition"),
    )
