"""Single source of truth for which provider names CADET knows about. A
provider module is a plain Python module (not a class) exposing NAME,
AGENT_ID, DISPLAY_NAME, build_argv, spawn, parse_error — see agy.py for the
reference shape. "agy", "codex", "cursor", and "copilot" are all wired up for
real now, each gated on its own empirical validation pass against the real
installed binary before being added here."""
from cadet.process.providers import agy, codex, copilot, cursor

DEFAULT_PROVIDER = "agy"

_PROVIDERS = {
    "agy": agy,
    "codex": codex,
    "cursor": cursor,
    "copilot": copilot,
}


def names() -> tuple:
    return tuple(_PROVIDERS)


def get(name):
    key = name or DEFAULT_PROVIDER
    if key not in _PROVIDERS:
        raise ValueError(f"unknown provider: {key!r}. Supported: {', '.join(names())}")
    return _PROVIDERS[key]
