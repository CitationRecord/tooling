"""One HTTP exchange, captured as bytes rather than described.

"The full request as sent" is a stronger claim than most clients can support.
A vendor SDK builds the body itself, so recording what was handed to the SDK
records an intention, not a request. `requests` prepares the request before it
goes out and keeps it on the response, so `response.request.body` is the bytes
that crossed the wire and `response.content` is the bytes that came back.

That is why this component speaks HTTP directly rather than through four
vendor SDKs: it buys uniform `captured` fidelity across every system instead
of a guarantee that varies by vendor. The fidelity is declared on every
transcript either way, because a record that cannot say which it has is worth
less than one that can.

Nothing here knows what any of it means. It sends bytes and returns bytes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from ..config import redact

#: Headers whose values are credentials. Scrubbed by name as well as by value,
#: because a key that never passed through `credential()` is still a key.
SECRET_HEADERS = frozenset({
    "x-api-key", "authorization", "x-goog-api-key", "api-key",
})


def iso_utc() -> str:
    return (datetime.now(timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def safe_headers(headers) -> dict:
    """Headers with every credential removed, by name and then by value."""
    clean = {}
    for name, value in dict(headers or {}).items():
        if name.lower() in SECRET_HEADERS:
            clean[name] = "<redacted>"
        else:
            clean[name] = redact(str(value))
    return clean


@dataclass
class Attempt:
    """What one send did. Recorded whether it worked or not."""

    started_at_utc: str
    finished_at_utc: str
    duration_ms: int
    http_status: int = None
    error: str = None

    def as_dict(self) -> dict:
        return {
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "duration_ms": self.duration_ms,
            "http_status": self.http_status,
            "error": self.error,
        }


@dataclass
class Wire:
    """The exchange, verbatim, plus every attempt that preceded it."""

    method: str
    url: str
    request_headers: dict
    request_body: str
    http_status: int = None
    response_headers: dict = field(default_factory=dict)
    response_body: str = ""
    fidelity: str = "captured"
    transport_error: str = None
    attempts: list = field(default_factory=list)
    started_at_utc: str = None
    finished_at_utc: str = None
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.http_status is not None and 200 <= self.http_status < 300

    def as_dict(self) -> dict:
        return {
            "request": {
                "method": self.method,
                "url": redact(self.url),
                "headers": self.request_headers,
                "body": self.request_body,
            },
            "response": {
                "http_status": self.http_status,
                "headers": self.response_headers,
                "body": self.response_body,
            },
            "wire_fidelity": self.fidelity,
            "transport_error": self.transport_error,
            "timing": {
                "started_at_utc": self.started_at_utc,
                "finished_at_utc": self.finished_at_utc,
                "duration_ms": self.duration_ms,
            },
            "attempts": [a.as_dict() for a in self.attempts],
        }


def _body_text(body) -> str:
    """The prepared body as text. Bytes are what `requests` actually sends."""
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return str(body)


def send(session, method: str, url: str, headers: dict, body: bytes,
         timeout: float, max_retries: int = 3, sleep=time.sleep) -> Wire:
    """Send once, retrying transport failures only, and record every attempt.

    **Nothing the model says is ever retried.** A refusal, an empty body, a
    malformed body and a 400 are all answers: they are returned as they are.
    Only a connection failing, a timeout, a 429 or a 5xx is retried, because
    none of those carries a response from a model.
    """
    wire = Wire(
        method=method,
        url=url,
        request_headers=safe_headers(headers),
        request_body=_body_text(body),
        started_at_utc=iso_utc(),
    )

    attempts = 0
    while True:
        attempts += 1
        started = time.monotonic()
        started_iso = iso_utc()
        try:
            response = session.request(
                method, url, headers=headers, data=body, timeout=timeout)
        except requests.RequestException as exc:
            duration = int((time.monotonic() - started) * 1000)
            message = redact(f"{exc.__class__.__name__}: {exc}")
            wire.attempts.append(Attempt(started_iso, iso_utc(), duration,
                                         error=message))
            if attempts <= max_retries:
                sleep(min(2 ** attempts, 20))
                continue
            wire.transport_error = message
            wire.finished_at_utc = iso_utc()
            wire.duration_ms = sum(a.duration_ms for a in wire.attempts)
            return wire

        duration = int((time.monotonic() - started) * 1000)
        status = response.status_code
        wire.attempts.append(Attempt(started_iso, iso_utc(), duration,
                                     http_status=status))

        if status == 429 or status >= 500:
            if attempts <= max_retries:
                sleep(_retry_after(response, attempts))
                continue

        # The request as prepared is the request as sent. Read it off the
        # response rather than re-serialising what we meant to send.
        sent = getattr(response, "request", None)
        if sent is not None:
            wire.url = redact(str(getattr(sent, "url", url)))
            wire.request_headers = safe_headers(getattr(sent, "headers", headers))
            wire.request_body = _body_text(getattr(sent, "body", body))

        wire.http_status = status
        wire.response_headers = safe_headers(response.headers)
        wire.response_body = redact(response.content.decode(
            response.encoding or "utf-8", errors="replace"))
        wire.finished_at_utc = iso_utc()
        wire.duration_ms = sum(a.duration_ms for a in wire.attempts)
        return wire


def _retry_after(response, attempts: int) -> float:
    header = (response.headers or {}).get("Retry-After")
    if header:
        try:
            return min(float(header), 120.0)
        except (TypeError, ValueError):
            pass
    return min(2 ** attempts, 30)
