"""Resolution and coverage cache.

SQLite in the output directory. Two tables: one for resolutions keyed by the
normalized citation, one for CourtListener holdings keyed by reporter and
volume, which are shared across every citation into the same book.

Errors are never cached. Caching a rate limit reply would turn a transient
throttle into a permanent finding about a citation.

Negative results expire; a resolved case does not. CourtListener ingests new
opinions continuously, so a "not found" recorded months ago may simply be
older than the data. Every entry carries the UTC time it was retrieved, so a
cached answer can always be dated.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import STATUS_ERROR, STATUS_RESOLVED, Resolution

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS resolutions (
    citation_key   TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    retrieved_at   TEXT NOT NULL,
    payload        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courts (
    court_id       TEXT PRIMARY KEY,
    full_name      TEXT,
    retrieved_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage (
    reporter       TEXT NOT NULL,
    volume         TEXT NOT NULL,
    opinion_count  INTEGER NOT NULL,
    retrieved_at   TEXT NOT NULL,
    PRIMARY KEY (reporter, volume)
);
"""

# Sentinel for the reporter-wide row, since SQLite primary keys reject NULL.
ALL_VOLUMES = "*"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def citation_key(text: str) -> str:
    """Cache key: whitespace-collapsed, case-folded citation."""
    return " ".join((text or "").split()).casefold()


class Cache:
    def __init__(self, path: Path, negative_max_age_days: int = 30) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._max_age = timedelta(days=negative_max_age_days)
        self._connection = sqlite3.connect(str(self.path))
        self._connection.row_factory = sqlite3.Row
        with closing(self._connection.cursor()) as cursor:
            cursor.executescript(SCHEMA_SQL)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "Cache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- resolutions -------------------------------------------------------

    def get(self, citation: str) -> Resolution | None:
        row = self._connection.execute(
            "SELECT status, retrieved_at, payload FROM resolutions WHERE citation_key = ?",
            (citation_key(citation),),
        ).fetchone()
        if row is None:
            return None

        if self._is_stale(row["status"], row["retrieved_at"]):
            return None

        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

        resolution = Resolution(**payload)
        resolution.from_cache = True
        resolution.cached_at_utc = row["retrieved_at"]
        return resolution

    def put(self, resolution: Resolution) -> bool:
        """Store a resolution. Errors are refused. Returns whether it stored."""
        if resolution.status == STATUS_ERROR:
            return False
        payload = dict(resolution.as_dict())
        payload["from_cache"] = False
        payload["cached_at_utc"] = None
        self._connection.execute(
            "INSERT OR REPLACE INTO resolutions "
            "(citation_key, status, retrieved_at, payload) VALUES (?, ?, ?, ?)",
            (
                citation_key(resolution.query),
                resolution.status,
                resolution.retrieved_at_utc,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        self._connection.commit()
        return True

    def _is_stale(self, status: str, retrieved_at: str) -> bool:
        if status == STATUS_RESOLVED:
            return False
        moment = _parse(retrieved_at)
        if moment is None:
            return True
        return _now() - moment > self._max_age

    # -- coverage ----------------------------------------------------------

    def get_coverage(self, reporter: str, volume: str | None) -> int | None:
        row = self._connection.execute(
            "SELECT opinion_count, retrieved_at FROM coverage WHERE reporter = ? AND volume = ?",
            (reporter, volume or ALL_VOLUMES),
        ).fetchone()
        if row is None:
            return None
        moment = _parse(row["retrieved_at"])
        if moment is None or _now() - moment > self._max_age:
            return None
        return int(row["opinion_count"])

    def put_coverage(self, reporter: str, volume: str | None, count: int) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO coverage "
            "(reporter, volume, opinion_count, retrieved_at) VALUES (?, ?, ?, ?)",
            (
                reporter,
                volume or ALL_VOLUMES,
                int(count),
                _now().isoformat(timespec="microseconds").replace("+00:00", "Z"),
            ),
        )
        self._connection.commit()

    # -- courts ------------------------------------------------------------
    # A court's name does not change, so these rows never expire.

    def get_court(self, court_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT full_name FROM courts WHERE court_id = ?", (court_id,)
        ).fetchone()
        return row["full_name"] if row else None

    def put_court(self, court_id: str, full_name: str | None) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO courts (court_id, full_name, retrieved_at) VALUES (?, ?, ?)",
            (court_id, full_name, _now().isoformat(timespec="microseconds").replace("+00:00", "Z")),
        )
        self._connection.commit()

    # -- maintenance -------------------------------------------------------

    def stats(self) -> dict:
        counts = {
            row["status"]: row["n"]
            for row in self._connection.execute(
                "SELECT status, COUNT(*) AS n FROM resolutions GROUP BY status"
            )
        }
        coverage_rows = self._connection.execute(
            "SELECT COUNT(*) AS n FROM coverage"
        ).fetchone()["n"]
        court_rows = self._connection.execute(
            "SELECT COUNT(*) AS n FROM courts"
        ).fetchone()["n"]
        return {
            "path": str(self.path),
            "resolutions": sum(counts.values()),
            "by_status": counts,
            "coverage_rows": coverage_rows,
            "court_rows": court_rows,
            "negative_max_age_days": self._max_age.days,
        }

    def clear(self) -> None:
        self._connection.executescript(
            "DELETE FROM resolutions; DELETE FROM coverage; DELETE FROM courts;"
        )
        self._connection.commit()
