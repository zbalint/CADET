"""Cursor CLI provider. Empirically validated 2026-07-26 against the real
installed binary (`cursor-agent`, Windows, free-tier account):
- **Critical — `cursor_path` must point at `cursor-agent.ps1`, invoked via
  `powershell.exe -File`, NOT at `cursor-agent.cmd` invoked directly.** The
  vendor's own `.cmd` is a thin `cmd.exe` batch wrapper (`... %*`) around the
  real `cursor-agent.ps1`. When Python's `asyncio.create_subprocess_exec`
  targets a `.cmd` file directly on Windows, the OS routes it through
  `cmd.exe`, which re-parses the *entire* command line for its own
  metacharacters (`<`, `>`, `&`, `|`, `^`) BEFORE the batch script ever sees
  `%*` — this happens regardless of argv-list quoting, because it's cmd.exe's
  line-level redirection parsing, not shell-string interpolation. CADET's own
  rendered prompt template (`prompt/template.py`) contains a literal
  `<one-line summary + outcome>` placeholder, so **every real `delegate_task`
  call for this provider silently corrupted its own argv** — reproduced
  directly: a trivial ASCII-only prompt worked fine through the `.cmd`, but
  the actual rendered CADET prompt (going through the identical `spawn()`
  code path) made `--trust` effectively vanish, producing the exact
  "Workspace Trust Required" exit-1 banner described below on every job,
  100% of the time. Invoking `powershell.exe -NoProfile -ExecutionPolicy
  Bypass -File <path-to-cursor-agent.ps1> <same argv>` instead — bypassing
  `cmd.exe` entirely — was confirmed via a live run with the real rendered
  prompt to work correctly (file written, correct content, real SALTMDB
  tool calls executed per the prompt's own instructions). `-File`'s argument
  forwarding passes each argument to the script literally; PowerShell does
  not re-interpret `<`/`>` there the way `cmd.exe` does. `cursor-agent.ps1`
  itself dynamically resolves the latest installed version under
  `<install-dir>/versions/<version>/`, so pointing at the `.ps1` (a stable
  path) rather than a hardcoded versioned `node.exe`+`index.js` pair still
  survives `cursor-agent update`. **`CADET_CURSOR_PATH` must be set to the
  `cursor-agent.ps1` path, not the `.cmd`.**
- Real jobs take noticeably longer to actually exit than to finish their
  visible work — confirmed one run where the model's conversation (including
  real SALTMDB tool calls) was fully done and the target file written well
  under a minute in, but `proc.wait()` didn't resolve until ~47s. Not an
  infinite hang (it did return, exit code `0`) — CADET's own per-job
  `timeout_s` + `kill_process_tree` already covers the worst case regardless,
  same as any other provider.
- `cursor-agent -p "<prompt>" --workspace <cwd>` on a directory the CLI has
  never seen before either prints an explicit "Workspace Trust Required"
  banner and exits 1 (no `--trust`/`--force`/`--yolo` given at all), or —
  once *any* `--trust`/`--force` has ever been used on this machine — skips
  that banner but still silently declines the underlying edit tool call
  while the model's own final text dishonestly claims success (exit 0,
  e.g. "Created foo.txt..." with no file on disk). Confirmed via a clean
  A/B: `--force` alone was insufficient on a brand-new directory; adding
  `--trust` alongside it made the edit actually apply. **Both flags must be
  passed together** — neither one alone is reliable for a directory CADET
  hasn't targeted before.
- `--mode plan` genuinely blocks edits (confirmed: no file written, no false
  "created" claim) even with `--trust --force` also present — this is the
  real safe/read-only lever for this provider, not `--sandbox` (see below).
- `--sandbox enabled` hard-errors immediately on Windows ("Sandbox requires
  macOS or Linux") — never pass it on this platform.
- Without `--trust --force`, `sandbox=False`'s natural "just allow edits,
  still gate risky commands" middle ground does not work headlessly (no TTY
  to approve): edits silently no-op with a false success claim, same
  "known non-functional combo" class as codex's workspace-write on Windows.
  Use skip_permissions=True for real edits.
- `--model` must ALWAYS be passed explicitly (default `"auto"` when CADET's
  own model param is unset) — omitting it inherits whatever model string was
  last selected *globally* across every `cursor-agent` invocation on the
  machine (persisted in `~/.cursor/cli-config.json`), including outside
  CADET. Confirmed the hard way: passing an account-unavailable named model
  once during validation silently became the sticky default for every
  subsequent call (including plain interactive use) until explicitly reset
  with `--model auto`. Free-tier accounts can only use `"auto"`.
- Effort is not a separate flag — the vendor docs describe bracket overrides
  on the model string itself (`'claude-opus-4-8[context=1m,effort=high]'`).
  UNCONFIRMED against a real call (this account is free-tier and can only
  use `auto`, which bracket overrides were never tested against); applied
  only when both model and effort are given.
- `cursor-agent` does not appear to block reading stdin the way codex does,
  but stdin is still explicitly redirected to DEVNULL as a defensive measure
  (this process is a long-running MCP server's subprocess; never let a
  child inherit its stdio transport pipe).
- No structured quota-exhaustion signal was found in vendor docs or during
  validation (this account never hit a real rate limit) — parse_error is
  best-effort only, same posture as codex's unconfirmed regex.
"""
import asyncio
import re
import subprocess
import sys
from datetime import datetime, timedelta

NAME = "cursor"
AGENT_ID = "cursor"
DISPLAY_NAME = "Cursor CLI"

# Absolute, not "powershell.exe" bare — an MCP-launched server process doesn't
# reliably inherit the interactive-shell PATH (same gotcha CADET already
# works around for CADET_AGY_PATH). This is Windows' own fixed install
# location for the version.1 PowerShell that ships with every supported
# release, not something CADET_CURSOR_PATH needs to override.
_WINDOWS_POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# Best-effort only: not empirically confirmed (never observed a real quota
# exhaustion during validation, and forcing one would burn the pool). Not
# finding a match is not an error, same as agy/codex's quota parsers.
_QUOTA_PATTERN = re.compile(r"usage limit reached.*?(?:try again|resets?) (?:in |at )?([0-9dhms]+)", re.IGNORECASE | re.DOTALL)
_DURATION_PATTERN = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def build_argv(
    cursor_path: str, prompt_text: str, cwd: str, timeout_s: int,
    model=None, effort=None, skip_permissions=False, sandbox=True,
) -> list[str]:
    """Pure argv construction. `cursor-agent` has no CLI-level timeout flag —
    CADET's own asyncio.wait_for + kill_process_tree already enforces
    timeout_s independently, same as the other providers.

    On Windows, `cursor_path` (expected to be `cursor-agent.ps1`, see module
    docstring) is wrapped in an explicit `powershell.exe -File` invocation
    rather than exec'd directly — exec'ing a `.cmd`/`.ps1` script directly
    routes through `cmd.exe`'s own line reparsing, which corrupts prompts
    containing `<`/`>`/`&`/`|`/`^` (CADET's rendered prompt template always
    contains a literal `<...>` placeholder). `-File` argument forwarding does
    not re-interpret those characters."""
    model_arg = model or "auto"
    if effort:
        model_arg = f"{model_arg}[effort={effort}]"
    cli_args = [
        "-p", prompt_text,
        "--workspace", cwd,
        "--output-format", "text",
        "--model", model_arg,
        "--trust",
    ]
    if skip_permissions:
        cli_args.append("--force")
    elif sandbox:
        cli_args += ["--mode", "plan"]
    # sandbox=False, skip_permissions=False falls through with neither flag:
    # confirmed non-functional on this platform (see module docstring) —
    # documented as a known caveat, not fixed here.
    if sys.platform == "win32":
        return [_WINDOWS_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cursor_path] + cli_args
    return [cursor_path] + cli_args


async def spawn(
    cursor_path: str, prompt_text: str, cwd: str, timeout_s: int, stdout_fh, stderr_fh,
    model=None, effort=None, skip_permissions=False, sandbox=True,
):
    """Spawn `cursor-agent -p` (via `powershell.exe -File` on Windows, see
    build_argv) as a background subprocess. stdin is explicitly DEVNULL as a
    defensive measure (see module docstring)."""
    argv = build_argv(cursor_path, prompt_text, cwd, timeout_s, model, effort, skip_permissions, sandbox)
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
    message. UNCONFIRMED — no real cursor-agent rate-limit wording was
    observed during validation. Returns (None, None) on no match, which is
    the expected/common case, not an error."""
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
