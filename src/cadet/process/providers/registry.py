"""Single source of truth for which provider names CADET knows about. A
provider module is a plain Python module (not a class) exposing NAME,
AGENT_ID, DISPLAY_NAME, build_argv, spawn, parse_error — see agy.py for the
reference shape. "agy" and "codex" are wired up for real; cursor/copilot are
added here in their own later phases, each gated on an empirical validation
pass against the real installed binary."""
from cadet.process.providers import agy, codex

DEFAULT_PROVIDER = "agy"

_PROVIDERS = {
    "agy": agy,
    "codex": codex,
}


def names() -> tuple:
    return tuple(_PROVIDERS)


def get(name):
    key = name or DEFAULT_PROVIDER
    if key not in _PROVIDERS:
        raise ValueError(f"unknown provider: {key!r}. Supported: {', '.join(names())}")
    return _PROVIDERS[key]
