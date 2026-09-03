"""Where bulk data lands, and where it must never land.

Bulk files are tens of gigabytes of third-party data. They belong outside
every repository: inside `tooling` a stray `git add` could try to commit
them, and inside the claim archive they would sit among evidence files that
are meant to be immutable and individually hashed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import DEFAULT_GENERATION

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Directories the download must never write into, checked by containment.
FORBIDDEN_ROOTS = (
    REPO_ROOT,
    REPO_ROOT.parent / "claim-archive",
)

DEFAULT_ROOT = REPO_ROOT.parent / "bulk-data"


class UnsafeDestination(ValueError):
    """The chosen directory is inside a repository or the claim archive."""


def default_directory(generation: str = DEFAULT_GENERATION) -> Path:
    return DEFAULT_ROOT / generation


def check_destination(path: Path) -> Path:
    """Resolve a destination, refusing anywhere bulk data must not go."""
    resolved = Path(path).expanduser().resolve()
    for forbidden in FORBIDDEN_ROOTS:
        forbidden = Path(forbidden).resolve()
        if resolved == forbidden or forbidden in resolved.parents:
            raise UnsafeDestination(
                f"refusing to write bulk data to {resolved}: it is inside "
                f"{forbidden}. Bulk data belongs outside the repository and "
                "outside the claim archive."
            )
    return resolved


@dataclass(frozen=True)
class Config:
    generation: str = DEFAULT_GENERATION
    directory: Path = None
    timeout: float = 300.0
    keys: tuple = ()

    def resolved(self) -> "Config":
        directory = self.directory or default_directory(self.generation)
        return Config(
            generation=self.generation,
            directory=check_destination(directory),
            timeout=self.timeout,
            keys=tuple(self.keys),
        )

    @property
    def manifest_path(self) -> Path:
        return Path(self.directory) / "manifest.json"
