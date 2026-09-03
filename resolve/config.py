"""Run configuration and path resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# resolve/config.py -> resolve/ -> repository root
REPO_ROOT = Path(__file__).resolve().parent.parent

# Derived data lives outside the repository, like the claim archive.
DEFAULT_OUT_DIR = REPO_ROOT.parent / "citation-resolutions"

CACHE_NAME = "resolutions.sqlite3"
JOURNAL_NAME = "lookups.jsonl"

API_BASE = "https://www.courtlistener.com/api/rest/v4"


@dataclass(frozen=True)
class Config:
    out_dir: Path = DEFAULT_OUT_DIR
    api_base: str = API_BASE

    # Observed on a personal token: the throttle reply reports a single
    # "5/min" budget, and search calls throttle later citation lookups, so
    # the shared ceiling is what actually binds. Endpoint rates sit on top.
    shared_per_minute: float = 5.0
    lookup_per_minute: float = 60.0
    search_per_minute: float = 5.0

    # A throttle reply that names a lower limit is adopted at runtime, so a
    # tighter tier does not have to be configured in advance.

    timeout: float = 60.0
    max_retries: int = 3

    # Negative results go stale as CourtListener ingests more data. A
    # resolved case does not, so it is kept until asked to refresh.
    negative_max_age_days: int = 30
    use_cache: bool = True
    refresh: bool = False

    # A coverage probe costs two throttled search calls. Without it a 404
    # cannot be told apart from a coverage gap, so it is on by default.
    probe_coverage: bool = True

    # The court is on the docket, not the cluster, so naming it costs one
    # extra call per case and one per court, the latter cached forever.
    resolve_court: bool = True

    @property
    def cache_path(self) -> Path:
        return self.out_dir / CACHE_NAME

    @property
    def journal_path(self) -> Path:
        return self.out_dir / JOURNAL_NAME

    def resolved(self) -> "Config":
        return Config(**{**self.__dict__, "out_dir": Path(self.out_dir).expanduser().resolve()})
