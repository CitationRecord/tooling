"""Where runs are written, and the credentials that are never written there.

Runs go outside every git repository, including the private one query sets
end up in. `register/` already owns that rule and `sample/` already reuses it;
this reuses the same function rather than restating it, so there is one place
to change if it ever needs changing.

Raw model outputs are excluded from the tooling repository by the README: they
are large, regenerated each edition, and in some cases subject to vendor terms.
This is where they go instead.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from register.config import UnsafeLocation, check_no_repository  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = REPO_ROOT.parent

#: Runs. Not a repository, and the guard refuses it if it becomes one.
DEFAULT_RUNS_DIR = WORKSPACE / "runs"

#: Where protocols live. Inside the repository on purpose: a prompt protocol
#: is published before the results it produces, unlike a query set.
PROTOCOL_DIR = Path(__file__).resolve().parent / "protocols"

#: The registration journal. `register/` owns it; named here so `runner/`
#: can read it without importing a CLI.
from register.config import DEFAULT_JOURNAL  # noqa: E402,F401

USER_AGENT = "CitationRecord-runner/0.1 (+https://citationrecord.org)"

#: One request may take a while: these are long answers from slow models, and
#: a timeout that fires mid-generation costs a query and records nothing.
DEFAULT_TIMEOUT = 300.0

#: Transport failures only. Nothing a model says is ever retried.
DEFAULT_MAX_RETRIES = 3


def runs_dir(path=None) -> Path:
    """Where this run writes, refusing anywhere inside a repository."""
    return Path(check_no_repository(Path(path or DEFAULT_RUNS_DIR)))


_SECRETS: set = set()


def remember_secret(value: str) -> None:
    """Hold a credential so `redact` can scrub it from anything written."""
    value = (value or "").strip()
    if len(value) >= 8:
        _SECRETS.add(value)


def redact(text: str) -> str:
    """Remove every known credential from a string on its way out.

    Applied to headers, transport errors and any response body that might
    echo a key back. A transcript is written once and never edited, so a
    credential that reaches one cannot be taken out again.
    """
    if not text:
        return text
    for secret in _SECRETS:
        text = text.replace(secret, "<redacted>")
    # Catch a key echoed inside a header shape even if it was never loaded
    # through this process, and an OAuth bearer token.
    text = re.sub(r"(?i)(x-api-key\s*[:=]\s*)\S+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+", r"\1<redacted>",
                  text)
    text = re.sub(r"(?i)([?&]key=)[^&\s\"']+", r"\1<redacted>", text)
    return text


def credential(env_var: str) -> str | None:
    """Read one credential from the environment, and remember it for redaction."""
    value = (os.environ.get(env_var) or "").strip()
    if not value:
        return None
    remember_secret(value)
    return value


def load_env(dotenv_path: Path | None = None) -> None:
    """Load .env if python-dotenv is available. Real env vars always win."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(dotenv_path or (REPO_ROOT / ".env"), override=False)


def shadowed_credentials(dotenv_path: Path | None = None) -> list:
    """Names whose .env value is being overridden by the environment.

    An exported variable beats `.env`, which is the right precedence and is
    also silent, and silence here is expensive. A key pasted into `.env` while
    an older one sits exported looks exactly like a key that does not work:
    the request fails, the message is about credentials, and the obvious
    conclusion is about the new key rather than about which key was sent.

    This does not change the precedence. It reports the disagreement so that a
    failure gets diagnosed against the credential that was actually used.
    Values are never returned or printed, only the names.
    """
    path = Path(dotenv_path or (REPO_ROOT / ".env"))
    if not path.is_file():
        return []
    shadowed = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not value:
            continue
        current = os.environ.get(name)
        if current is not None and current.strip() != value:
            shadowed.append(name)
    return shadowed


@dataclass(frozen=True)
class Config:
    runs: Path = None
    journal: Path = None
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES

    def resolved(self) -> "Config":
        return Config(
            runs=Path(self.runs or DEFAULT_RUNS_DIR).expanduser(),
            journal=Path(self.journal or DEFAULT_JOURNAL).expanduser().resolve(),
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
