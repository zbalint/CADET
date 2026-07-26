"""Codex CLI provider. Empirically validated 2026-07-26 against the real
installed binary (codex 1.1.7-equivalent, Windows):
- `codex exec "<prompt>" -C <cwd> -s workspace-write ...` silently no-ops on
  Windows — the sandbox's write-enforcement helper (codex-windows-sandbox-setup.exe)
  fails to launch, so exit code is 0 but no file is written. Do not rely on
  workspace-write for real edits on Windows until that's fixed upstream.
- `-s read-only` runs cleanly (vendor default; genuinely safe, confirmed via a
  real invocation).
- `--dangerously-bypass-approvals-and-sandbox` is the only flag confirmed to
  actually apply edits headlessly on this platform — mapped to CADET's existing
  `skip_permissions` knob, mirroring agy's `--dangerously-skip-permissions`.
- `codex exec` always attempts to read stdin ("Reading additional input from
  stdin...") even when a prompt is given as an argv arg. A live subprocess
  spawned from the long-running MCP server would otherwise inherit the
  server's own stdio transport pipe as its stdin — spawn() explicitly
  redirects stdin to DEVNULL to guarantee immediate EOF instead.
- `-m/--model` and `-c model_reasoning_effort=<value>` both confirmed to parse
  and take effect via a real invocation.
"""
import asyncio
import re
import subprocess
import sys
from datetime import datetime, timedelta

NAME = "codex"
AGENT_ID = "codex"
DISPLAY_NAME = "Codex CLI"

# Best-effort only: not empirically confirmed (never observed a real quota
# exhaustion during validation, and forcing one would burn the pool). Not
# finding a match is not an error, same as agy's parse_quota_exhaustion.
_QUOTA_PATTERN = re.compile(r"usage limit reached.*?(?:try again|resets?) (?:in |at )?([0-9dhms]+)", re.IGNORECASE | re.DOTALL)
_DURATION_PATTERN = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def build_argv(
    codex_path: str, prompt_text: str, cwd: str, timeout_s: int,
    model=None, effort=None, skip_permissions=False, sandbox=True,
) -> list[str]:
    """Pure argv construction, mirroring launcher.py's build_argv shape.
    `codex exec` has no CLI-level timeout flag — CADET's own asyncio.wait_for
    + kill_process_tree already enforces timeout_s independently, same as for
    agy's --print-timeout being belt-and-suspenders rather than load-bearing."""
    argv = [
        codex_path, "exec", prompt_text,
        "-C", cwd,
        "--skip-git-repo-check",
        "--json",
    ]
    if skip_permissions:
        argv.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        argv += ["-s", "read-only" if sandbox else "workspace-write"]
    if model:
        argv += ["-m", model]
    if effort:
        argv += ["-c", f"model_reasoning_effort={effort}"]
    return argv


async def spawn(
    codex_path: str, prompt_text: str, cwd: str, timeout_s: int, stdout_fh, stderr_fh,
    model=None, effort=None, skip_permissions=False, sandbox=True,
):
    """Spawn `codex exec` as a background subprocess. stdin is explicitly
    DEVNULL — codex always probes stdin for extra input, and this process
    otherwise inherits the CADET MCP server's own stdio transport pipe (see
    module docstring)."""
    argv = build_argv(codex_path, prompt_text, cwd, timeout_s, model, effort, skip_permissions, sandbox)
    spawn_kwargs = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout_fh, stderr=stderr_fh)
    if sys.platform == "win32":
        spawn_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        spawn_kwargs["start_new_session"] = True
    return await asyncio.create_subprocess_exec(*argv, **spawn_kwargs)


def _parse_duration_to_seconds(duration_str: str):
    match = _DURATION_PATTERN.fullmatch(duration_str)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_error(stderr_tail: str, finished_at_iso: str):
    """Best-effort scan of a failed job's stderr tail for a quota-exhaustion
    message. Unlike agy's regex (empirically confirmed against real vendor
    text), this pattern is UNCONFIRMED — codex's exact rate-limit wording
    wasn't observed during validation. Returns (None, None) on no match,
    which is the expected/common case, not an error."""
    match = _QUOTA_PATTERN.search(stderr_tail or "")
    if not match:
        return None, None
    seconds = _parse_duration_to_seconds(match.group(1))
    if seconds is None:
        return None, None
    finished_dt = datetime.fromisoformat(finished_at_iso)
    reset_dt = finished_dt + timedelta(seconds=seconds)
    return "quota_exhausted", reset_dt.isoformat()


__all__ = ["NAME", "AGENT_ID", "DISPLAY_NAME", "build_argv", "spawn", "parse_error"]
