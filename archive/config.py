"""Run configuration and path resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# archive/config.py -> archive/ -> repository root
REPO_ROOT = Path(__file__).resolve().parent.parent

# Output lives outside the repository. Snapshots are large, are regenerated
# each edition, and in some cases carry vendor terms of use.
DEFAULT_OUT_DIR = REPO_ROOT.parent / "claim-archive"
DEFAULT_CLAIMS_PATH = REPO_ROOT / "archive" / "claims.yaml"

MANIFEST_NAME = "manifest.jsonl"
SNAPSHOT_DIRNAME = "snapshots"


@dataclass(frozen=True)
class Config:
    """Everything a capture run needs, resolved to absolute paths."""

    claims_path: Path
    out_dir: Path
    only: tuple[str, ...] = ()
    submit_wayback: bool = True
    wayback_timeout: float = 90.0
    page_timeout_ms: int = 45_000
    robots_timeout: float = 20.0
    settle_ms: int = 1_500
    wait_until: str = "networkidle"
    full_page: bool = True
    viewport_width: int = 1440
    viewport_height: int = 900
    min_delay: float = 2.0
    dry_run: bool = False
    headless: bool = True

    @property
    def manifest_path(self) -> Path:
        return self.out_dir / MANIFEST_NAME

    @property
    def snapshot_root(self) -> Path:
        return self.out_dir / SNAPSHOT_DIRNAME

    def resolved(self) -> "Config":
        return Config(
            **{
                **self.__dict__,
                "claims_path": Path(self.claims_path).expanduser().resolve(),
                "out_dir": Path(self.out_dir).expanduser().resolve(),
            }
        )
