"""robots.txt evaluation.

Design constraint: nothing misrepresents itself and no data-collection
objective outranks it. There is deliberately no override flag. If a site
disallows the archiver, the run records the refusal and moves on.
"""

from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

from . import USER_AGENT


@dataclass(frozen=True)
class RobotsVerdict:
    allowed: bool
    reason: str
    robots_url: str
    http_status: int | None = None
    crawl_delay: float | None = None


class RobotsCache:
    """Fetches robots.txt once per origin per run."""

    def __init__(self, timeout: float = 20.0, user_agent: str = USER_AGENT) -> None:
        self._timeout = timeout
        self._user_agent = user_agent
        self._cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._failures: dict[str, RobotsVerdict] = {}

    def check(self, url: str) -> RobotsVerdict:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        robots_url = urljoin(origin, "/robots.txt")

        if origin in self._failures:
            failure = self._failures[origin]
            return RobotsVerdict(
                allowed=failure.allowed,
                reason=failure.reason,
                robots_url=robots_url,
                http_status=failure.http_status,
            )

        parser = self._cache.get(origin)
        if parser is None:
            parser, verdict = self._load(robots_url)
            if parser is None:
                self._failures[origin] = verdict
                return verdict
            self._cache[origin] = parser

        delay = parser.crawl_delay(self._user_agent)
        allowed = parser.can_fetch(self._user_agent, url)
        return RobotsVerdict(
            allowed=allowed,
            reason="allowed by robots.txt" if allowed else "disallowed by robots.txt",
            robots_url=robots_url,
            http_status=200,
            crawl_delay=float(delay) if delay is not None else None,
        )

    def _load(
        self, robots_url: str
    ) -> tuple[urllib.robotparser.RobotFileParser | None, RobotsVerdict]:
        try:
            response = requests.get(
                robots_url,
                headers={"User-Agent": self._user_agent, "Accept": "text/plain"},
                timeout=self._timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            # RFC 9309: robots.txt unreachable means assume disallowed.
            return None, RobotsVerdict(
                allowed=False,
                reason=f"robots.txt unreachable ({exc.__class__.__name__}), treating as disallow",
                robots_url=robots_url,
            )

        if response.status_code >= 500:
            return None, RobotsVerdict(
                allowed=False,
                reason=f"robots.txt returned HTTP {response.status_code}, treating as disallow",
                robots_url=robots_url,
                http_status=response.status_code,
            )

        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        if response.status_code >= 400:
            # RFC 9309: unavailable (4xx) means no restrictions.
            parser.parse([])
        else:
            parser.parse(response.text.splitlines())
        return parser, RobotsVerdict(
            allowed=True, reason="parsed", robots_url=robots_url,
            http_status=response.status_code,
        )
