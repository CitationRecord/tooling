"""Where an unpublished artifact may live, and where it must never.

The queries for an edition must not reach a public repository before that
edition ships. Publishing them in advance would make contamination of later
editions certain rather than possible, so this is a correctness rule, not a
preference, and it is enforced rather than remembered.

Two guards, because either alone leaks. The named list refuses the public
repositories this project actually has. The generic check refuses any git
repository that is not the one private repository, which catches the ones
nobody thought to name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The project directory holding every repository.
WORKSPACE = REPO_ROOT.parent

#: The one repository an unpublished artifact may live in. Private until the
#: edition ships, then made public with its history intact.
PRIVATE_ROOT = WORKSPACE / "queries"

#: Public repositories, by name. Checked by containment, so a subdirectory of
#: any of these is refused too.
FORBIDDEN_ROOTS = (
    REPO_ROOT,                          # tooling
    WORKSPACE / "site",                 # citationrecord.org
    WORKSPACE / "citation-resolutions", # the lookup journal
    WORKSPACE / "claim-archive",        # vendor claim snapshots
)

#: Where registrations are written. Public on purpose: the journal is the
#: control, and a control nobody can read controls nothing.
DEFAULT_JOURNAL = WORKSPACE / "citation-resolutions" / "registrations.jsonl"


class UnsafeLocation(ValueError):
    """The artifact is somewhere an unpublished artifact must not be."""


def _git_root(path: Path) -> Path | None:
    """The repository containing a path, or None if it is in none."""
    for candidate in [path, *path.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def check_unpublished(path, private_root=None, forbidden_roots=None) -> Path:
    """Resolve an artifact path, refusing anywhere it must not be.

    Applied to the artifact being registered, not to the journal. The journal
    is meant to be public; the artifact is not, until its edition ships.
    """
    resolved = Path(path).expanduser().resolve()
    private = Path(private_root or PRIVATE_ROOT).expanduser().resolve()
    roots = FORBIDDEN_ROOTS if forbidden_roots is None else forbidden_roots

    for root in roots:
        root = Path(root).expanduser().resolve()
        if resolved == root or root in resolved.parents:
            raise UnsafeLocation(
                f"refusing to register {resolved}: it is inside {root}, which "
                "is a public repository. An unpublished query set in a public "
                "repository is published, whatever the intent."
            )

    enclosing = _git_root(resolved)
    if enclosing is not None and enclosing.resolve() != private:
        raise UnsafeLocation(
            f"refusing to register {resolved}: it is inside the git repository "
            f"at {enclosing}, which is not the private artifact repository at "
            f"{private}. Move it there, or name the private root explicitly if "
            "this repository is genuinely private."
        )
    return resolved


@dataclass(frozen=True)
class Config:
    journal: Path = None
    private_root: Path = None

    def resolved(self) -> "Config":
        return Config(
            journal=Path(self.journal or DEFAULT_JOURNAL).expanduser().resolve(),
            private_root=Path(self.private_root or PRIVATE_ROOT).expanduser().resolve(),
        )
