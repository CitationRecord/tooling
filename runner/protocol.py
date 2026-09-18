"""Loading a protocol, hashing it, and asking whether it was registered.

The protocol's identity is its canonical hash, not its filename and not the
version string inside it. The version string is how people refer to it; the
hash is what makes the reference mean something. Both travel on every
transcript, along with the canonical form, so a third party can recompute the
hash from the record without reading this source.

Hashing reuses `register/`'s digest rather than reimplementing it. Two
implementations of one canonical form is one implementation too many: they
would agree until the day they did not, and the day they did not would look
exactly like tampering.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from register.digest import Digest, digest_file
from register.journal import find_by_digest

from .config import PROTOCOL_DIR


class ProtocolError(ValueError):
    """The protocol is missing, unreadable, or not what it says it is."""


@dataclass(frozen=True)
class Protocol:
    """A frozen protocol, its hash, and where it came from."""

    version: str
    path: Path
    body: dict
    digest: Digest

    @property
    def system_prompt(self):
        return (self.body.get("system_prompt") or {}).get("text") or None

    def block(self, system_id: str) -> dict:
        """This system's section of the protocol."""
        block = (self.body.get("systems") or {}).get(system_id)
        if not block:
            raise ProtocolError(
                f"protocol {self.version} has no section for system "
                f"{system_id!r}. A system with no declared model, ceiling and "
                f"sampling policy is a system nothing was frozen about.")
        for required in ("model", "max_output_tokens"):
            if not block.get(required):
                raise ProtocolError(
                    f"protocol {self.version}, system {system_id!r}: no "
                    f"{required}")
        return block

    def as_dict(self) -> dict:
        """What every transcript records about the protocol that made it."""
        return {
            "version": self.version,
            "filename": self.path.name,
            "sha256_canonical": self.digest.sha256_canonical,
            "sha256_bytes": self.digest.sha256_bytes,
            "canonical_form": self.digest.canonical_form,
            "controls": self.digest.controlling_kind,
        }


def path_for(version: str, directory=None) -> Path:
    directory = Path(directory or PROTOCOL_DIR)
    return directory / f"prompt-protocol-{version}.json"


def available(directory=None) -> list:
    """Every protocol file on disk, by version, in order."""
    directory = Path(directory or PROTOCOL_DIR)
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("prompt-protocol-*.json")):
        found.append(path.name[len("prompt-protocol-"):-len(".json")])
    return found


def load(version: str, directory=None) -> Protocol:
    """Read a protocol, hash it, and check it is the one it claims to be."""
    path = Path(version)
    if not path.is_file():
        path = path_for(version, directory)
    if not path.is_file():
        raise ProtocolError(f"no protocol at {path}")

    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"{path.name} is not readable JSON: {exc}") from None
    if not isinstance(body, dict):
        raise ProtocolError(f"{path.name} is not a JSON object")

    declared = body.get("protocol_version")
    expected = path.name[len("prompt-protocol-"):-len(".json")]
    if declared != expected:
        # This is what catches somebody editing v1 in place and bumping the
        # version string inside it, or copying v1 to v2 and forgetting to.
        raise ProtocolError(
            f"{path.name} declares protocol_version {declared!r} but its "
            f"filename says {expected!r}. A protocol is never edited in "
            f"place: a change means a new file and a registration naming "
            f"--supersedes.")

    digest = digest_file(path)
    if not digest.sha256_canonical:
        raise ProtocolError(
            f"{path.name} did not parse as JSON for canonical hashing, so the "
            f"only available control would be a byte hash. A protocol is "
            f"written as JSON for exactly this reason.")

    return Protocol(version=expected, path=path, body=body, digest=digest)


def registration(protocol: Protocol, journal) -> dict | None:
    """The journal record fixing this protocol, or None if there is not one.

    Matched on the controlling hash, so a reformatted file with identical
    data still matches: that is what the canonical form is for.
    """
    records = find_by_digest(journal, protocol.digest)
    for record in reversed(records):
        if record.get("kind") == "prompt-protocol":
            return record
    return records[-1] if records else None
