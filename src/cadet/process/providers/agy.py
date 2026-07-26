"""Re-export shim: agy is the original/reference provider, so its real
implementation stays in launcher.py/quota.py untouched (and so do their
existing tests) rather than being duplicated here."""
from cadet.process.launcher import build_argv, spawn_agy as spawn
from cadet.process.quota import parse_quota_exhaustion as parse_error

NAME = "agy"
AGENT_ID = "antigravity"
DISPLAY_NAME = "Antigravity (agy)"

__all__ = ["NAME", "AGENT_ID", "DISPLAY_NAME", "build_argv", "spawn", "parse_error"]
