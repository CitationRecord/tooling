"""The systems under test, and which of them the page commits to.

This holds identity: what a system is called, whose API answers for it, which
credential reaches it, and whether it is in the published scope of the edition.
It does not hold what is asked of it. The model identifier, the sampling values
and the retrieval settings are in the protocol, because those are the things
that must be frozen and hashed before anything is called.

**Published scope is recorded, not assumed.** citationrecord.org commits to
three systems for Edition One: Claude, GPT-5 and Gemini. Grok is run
exploratorily and is not in that scope. The flag travels onto every transcript
and into the run manifest, so nothing downstream has to remember which was
which, and no result from an out-of-scope system can be quietly folded in.
"""

from __future__ import annotations

from dataclasses import dataclass

from .adapters import anthropic, google, openai, xai


@dataclass(frozen=True)
class System:
    """One system under test."""

    id: str
    vendor: str
    label: str
    env_var: str
    published_scope: bool
    adapter: object
    scope_note: str = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "vendor": self.vendor,
            "label": self.label,
            "published_scope": self.published_scope,
            "scope_note": self.scope_note,
            "retrieval_disabled_by": self.adapter.RETRIEVAL_OFF,
        }


#: The scope note is one sentence and it is attached to the system rather than
#: to a report, so it cannot be dropped in transit.
OUT_OF_SCOPE = (
    "Not in the published scope of Edition One. citationrecord.org states that "
    "the edition measures Claude, GPT-5 and Gemini. Results from this system "
    "are exploratory and must not be reported as part of an edition unless the "
    "page is changed first."
)

SYSTEMS = (
    System(
        id="claude",
        vendor="anthropic",
        label="Claude",
        env_var="ANTHROPIC_API_KEY",
        published_scope=True,
        adapter=anthropic,
    ),
    System(
        id="gpt-5",
        vendor="openai",
        label="GPT-5",
        env_var="OPENAI_API_KEY",
        published_scope=True,
        adapter=openai,
    ),
    System(
        id="gemini",
        vendor="google",
        label="Gemini",
        env_var="GEMINI_API_KEY",
        published_scope=True,
        adapter=google,
    ),
    System(
        id="grok",
        vendor="xai",
        label="Grok",
        env_var="XAI_API_KEY",
        published_scope=False,
        adapter=xai,
        scope_note=OUT_OF_SCOPE,
    ),
)

SYSTEM_IDS = tuple(system.id for system in SYSTEMS)

#: The three the page commits to, in the order the page names them.
PUBLISHED_IDS = tuple(s.id for s in SYSTEMS if s.published_scope)


def by_id(system_id: str) -> System:
    for system in SYSTEMS:
        if system.id == system_id:
            return system
    raise KeyError(f"no system {system_id!r}; known: {', '.join(SYSTEM_IDS)}")


def select(names) -> tuple:
    """Resolve a selection of systems.

    `all` is every system including the out-of-scope one, which is what a
    pilot wants. `published` is the three the edition commits to.
    """
    if not names or list(names) == ["all"]:
        return tuple(SYSTEMS)
    if list(names) == ["published"]:
        return tuple(s for s in SYSTEMS if s.published_scope)
    return tuple(by_id(name) for name in names)
