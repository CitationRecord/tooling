"""CourtListener API client.

The API token is read from the environment and nothing else. It is never a
default, never a CLI argument, never written to the cache, the journal, or an
error message. `redact` scrubs it from any string on its way out, so a
traceback carrying a URL with credentials cannot leak through the logs.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from . import TOKEN_ENV_VAR, USER_AGENT
from .config import Config

_TOKEN_CACHE: dict[str, str] = {}
_LOCK = threading.Lock()

# Rate limit replies carry the wait in prose: "Expected available in 4 seconds."
_RETRY_HINT = re.compile(r"available in (\d+(?:\.\d+)?)\s*second", re.I)
# "Rate limit exceeded: 5/min" names the real budget for this token.
_LIMIT_HINT = re.compile(r"Rate limit exceeded:\s*(\d+(?:\.\d+)?)\s*/\s*(min|hour|day)", re.I)


class ResolverError(RuntimeError):
    """Base class for lookup failures."""


class MissingToken(ResolverError):
    """No API token in the environment."""


class RateLimited(ResolverError):
    """CourtListener throttled the request. Says nothing about the citation."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TransportError(ResolverError):
    """Network failure or an unexpected HTTP status."""


def load_env(dotenv_path: Path | None = None) -> None:
    """Load .env if python-dotenv is available. Real env vars always win."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(dotenv_path, override=False)


def token() -> str:
    """Read the API token from the environment.

    Raises MissingToken with instructions rather than proceeding
    unauthenticated, because an anonymous request is throttled hard enough to
    look like a coverage gap.
    """
    value = (os.environ.get(TOKEN_ENV_VAR) or "").strip()
    if not value:
        raise MissingToken(
            f"{TOKEN_ENV_VAR} is not set. Put it in .env at the repository "
            "root or export it. Get a token at "
            "https://www.courtlistener.com/help/api/"
        )
    with _LOCK:
        _TOKEN_CACHE["value"] = value
    return value


def redact(text: str) -> str:
    """Replace the API token with a placeholder wherever it appears."""
    if not text:
        return text
    secret = _TOKEN_CACHE.get("value") or (os.environ.get(TOKEN_ENV_VAR) or "").strip()
    if secret and len(secret) >= 8:
        text = text.replace(secret, "<redacted>")
    # Catch a token echoed inside an Authorization header too.
    return re.sub(r"(Token\s+)[A-Za-z0-9._\-]{8,}", r"\1<redacted>", text)


@dataclass
class Call:
    """What one HTTP call did, for the journal. Carries no credentials."""

    endpoint: str
    method: str
    http_status: int | None
    duration_ms: int
    throttled: bool = False
    error: str | None = None


class _Throttle:
    """Minimum spacing between calls to one endpoint."""

    def __init__(self, per_minute: float) -> None:
        self.per_minute = per_minute
        self._interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            remaining = self._interval - (time.monotonic() - self._last)
            if remaining > 0:
                time.sleep(remaining)
            self._last = time.monotonic()


class CourtListener:
    """Thin, throttled, credential-safe wrapper over the v4 REST API."""

    def __init__(self, config: Config | None = None, session=None) -> None:
        self._config = config or Config()
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Token {token()}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )
        # CourtListener's throttle replies report one quota ("5/min") and a
        # burst of search calls will throttle a later citation lookup, so the
        # budget is shared rather than counted per endpoint. Endpoint
        # throttles sit on top of the shared one, never under it.
        self._shared = _Throttle(self._config.shared_per_minute)
        self._throttles = {
            "citation-lookup": _Throttle(self._config.lookup_per_minute),
            "search": _Throttle(self._config.search_per_minute),
            "clusters": _Throttle(self._config.lookup_per_minute),
            "dockets": _Throttle(self._config.lookup_per_minute),
            "courts": _Throttle(self._config.lookup_per_minute),
        }
        self.calls: list[Call] = []

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "CourtListener":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _throttle_for(self, endpoint: str) -> _Throttle:
        # "clusters/2812209" throttles as "clusters".
        root = endpoint.split("/", 1)[0]
        return self._throttles.get(root, _Throttle(self._config.lookup_per_minute))

    def _request(self, method: str, endpoint: str, **kwargs):
        url = f"{self._config.api_base}/{endpoint}/"
        attempts = 0
        while True:
            attempts += 1
            self._shared.wait()
            self._throttle_for(endpoint).wait()
            started = time.monotonic()
            try:
                response = self._session.request(
                    method, url, timeout=self._config.timeout, **kwargs
                )
            except requests.RequestException as exc:
                duration = int((time.monotonic() - started) * 1000)
                message = redact(f"{exc.__class__.__name__}: {exc}")
                self.calls.append(
                    Call(endpoint, method, None, duration, error=message)
                )
                if attempts <= self._config.max_retries:
                    time.sleep(min(2 ** attempts, 10))
                    continue
                raise TransportError(message) from None

            duration = int((time.monotonic() - started) * 1000)

            if response.status_code == 429:
                self._learn_limit(response)
                wait = self._retry_after(response)
                self.calls.append(
                    Call(endpoint, method, 429, duration, throttled=True)
                )
                if attempts <= self._config.max_retries:
                    time.sleep(wait)
                    continue
                raise RateLimited(
                    f"CourtListener rate limit reached on {endpoint} after "
                    f"{attempts} attempts",
                    retry_after=wait,
                )

            self.calls.append(Call(endpoint, method, response.status_code, duration))

            if response.status_code in (401, 403):
                raise MissingToken(
                    f"CourtListener rejected the API token (HTTP "
                    f"{response.status_code}). Check {TOKEN_ENV_VAR}."
                )
            if response.status_code >= 400:
                raise TransportError(
                    redact(f"HTTP {response.status_code} from {endpoint}")
                )
            return response

    def _learn_limit(self, response) -> None:
        """Adopt the limit CourtListener names in a throttle reply."""
        match = _LIMIT_HINT.search(response.text or "")
        if not match:
            return
        per_minute = float(match.group(1))
        if match.group(2).lower().startswith("h"):
            per_minute /= 60.0
        if per_minute > 0 and per_minute < self._shared.per_minute:
            self._shared = _Throttle(per_minute)

    @staticmethod
    def _retry_after(response) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), 120.0)
            except ValueError:
                pass
        match = _RETRY_HINT.search(response.text or "")
        if match:
            return min(float(match.group(1)) + 1.0, 120.0)
        return 15.0

    # -- endpoints ---------------------------------------------------------

    def lookup_citation(self, text: str) -> list:
        """POST /citation-lookup/. Returns one entry per citation found."""
        response = self._request("POST", "citation-lookup", data={"text": text})
        body = response.json()
        return body if isinstance(body, list) else []

    def search_count(self, query: str) -> int:
        """How many opinions match a citation query. Used for coverage."""
        response = self._request(
            "GET", "search", params={"type": "o", "q": query, "page_size": 1}
        )
        body = response.json()
        return int(body.get("count") or 0)

    def volume_holdings(self, reporter: str, volume, sample: int = 20) -> dict:
        """Exactly what CourtListener holds in one volume of one reporter.

        Uses the structured citation filter rather than a phrase match on
        citation text. A phrase query is a prefix match: citation:("347 U.S.")
        also matches "347 U.S. App. D.C.", inflating the count for any
        reporter whose name prefixes another. The filter matches the stored
        reporter field exactly.

        Costs one request, plus a second only when the volume holds more
        opinions than one page returns.
        """
        params = {
            "citations__reporter": reporter,
            "citations__volume": str(volume),
            "fields": "id,date_filed,citations",
            "page_size": sample,
        }
        body = self._request("GET", "clusters", params=params).json()
        results = body.get("results") or []

        pages = sorted(
            int(citation["page"])
            for result in results
            for citation in (result.get("citations") or [])
            if citation.get("reporter") == reporter
            and str(citation.get("page", "")).isdigit()
        )

        complete = not body.get("next")
        if complete:
            count = len(results)
        else:
            counted = self._request(
                "GET", "clusters", params={**params, "count": "on"}
            ).json()
            count = int(counted.get("count") or 0)

        return {
            "count": count,
            "pages": pages,
            "pages_are_complete": complete,
            "sampled": len(results),
        }

    def reporter_has_opinions(self, reporter: str) -> int:
        """Opinions visible on the first page for a reporter.

        A presence check, not a total: an exact count over a whole reporter
        forces a full scan and times out. Zero means CourtListener holds
        nothing in this reporter.
        """
        body = self._request(
            "GET", "clusters",
            params={"citations__reporter": reporter, "fields": "id", "page_size": 20},
        ).json()
        return len(body.get("results") or [])

    def search_citations(self, query: str, limit: int = 100) -> list:
        """Every citation string on the opinions matching a query.

        Used to find which pages of a volume CourtListener actually holds.
        A count alone cannot say whether the cited page falls inside the
        part of the volume that was ingested.
        """
        response = self._request(
            "GET", "search",
            params={"type": "o", "q": query, "page_size": min(limit, 100)},
        )
        found = []
        for result in response.json().get("results") or []:
            found.extend(result.get("citation") or [])
        return found

    def cluster(self, cluster_id: int) -> dict:
        response = self._request("GET", f"clusters/{cluster_id}")
        return response.json()

    def docket(self, docket_id: int) -> dict:
        """Where the court lives. A cluster carries only the docket id."""
        response = self._request("GET", f"dockets/{docket_id}")
        return response.json()

    def court(self, court_id: str) -> dict:
        response = self._request("GET", f"courts/{court_id}")
        return response.json()
