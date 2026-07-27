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
- **Confirmed 2026-07-27** (real free-tier account, hit during live multi-provider
  stress-testing — replaying `build_docker_argv`'s exact argv outside CADET's own
  dispatcher via a bare `subprocess.run`): the real exhaustion message is
  `"ActionRequiredError: You've hit your usage limit Get Cursor Pro for more
  Agent usage, unlimited Tab, and more."`, exit code 1, **no reset-time/ETA
  anywhere in the message** — unlike agy/codex, `quota_reset_at` cannot be
  derived for cursor even in principle from this wording. `parse_error`
  matches this via `_QUOTA_NO_RESET_PATTERN` and returns
  `("quota_exhausted", None)`. The original guessed shape (`_QUOTA_PATTERN_WITH_RESET`,
  which does expect a duration) is kept as a fallback in case a different
  plan tier emits reset-ETA wording, but has never itself been observed.

Containerized 2026-07-27 (Phase 4, mirroring codex's Phase 3 pattern — see
docs/ARCHITECTURE.md's "Containerized cursor execution" section). Fully
replaces the native Windows powershell.exe path above — no dual mode, same
decision already made for agy and codex:
- The official Linux installer (`curl https://cursor.com/install -fsS |
  bash`) places the binary at `~/.local/bin/agent` — **named `agent`, not
  `cursor-agent`**, despite the product being called "Cursor Agent CLI".
- **Auth: OAuth device-flow login into a dedicated Docker volume, same shape
  as agy's Phase 2 — live-verified, not the CURSOR_API_KEY-only design
  originally planned.** cursor-agent's Linux build stores its real session
  credential (`auth.json`) under the XDG config dir `~/.config/cursor/`, a
  completely different location than the Windows install's `~/.cursor/`
  (confirmed via a full-home-directory capture during a real login) — no
  cross-platform credential copy is possible, same "different OS, different
  credential file" situation agy hit. A one-time `agent login` against the
  `cadet-cursor-auth` volume (`CADET_CURSOR_AUTH_VOLUME`) is required before
  any headless job can authenticate; see build_docker_argv's docstring for
  the exact command. `CURSOR_API_KEY` is still forwarded when
  `CADET_CURSOR_API_KEY` is set (the CLI supports both), but is now optional,
  not required.
- The `--mode plan` / `--trust --force` argv-level semantics validated above
  (on native Windows) were **confirmed identical inside the Linux
  container** via two real live calls (a read-only Q&A call and a real
  file-write call, both against the OAuth-authenticated volume) before this
  was wired into CADET. The `/workspace` bind mount's `:ro`/`:rw` distinction
  still backs the read-only/read-write semantics as defense-in-depth,
  kernel VFS enforcement, same reasoning as codex's Phase 3.
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

# _QUOTA_PATTERN_WITH_RESET is the original best-effort guess (never observed
# during initial validation) -- kept as a fallback shape in case some cursor
# plan tier does emit a reset ETA. _QUOTA_NO_RESET_PATTERN is the real,
# empirically confirmed message (2026-07-27, real free-tier account, live
# job replay outside CADET's dispatcher): "ActionRequiredError: You've hit
# your usage limit Get Cursor Pro for more Agent usage, unlimited Tab, and
# more." -- exit code 1, no reset-time/ETA given anywhere in the message, so
# quota_reset_at legitimately stays None for this shape. This is intentionally
# cursor-specific: agy (quota.py), codex.py, and copilot.py each have their own
# independent quota wording and must NOT share this pattern -- every provider
# has been observed (or is expected) to signal exhaustion differently.
_QUOTA_PATTERN_WITH_RESET = re.compile(r"usage limit reached.*?(?:try again|resets?) (?:in |at )?([0-9dhms]+)", re.IGNORECASE | re.DOTALL)
_QUOTA_NO_RESET_PATTERN = re.compile(r"hit your usage limit", re.IGNORECASE)
_DURATION_PATTERN = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def build_argv(
    cursor_path: str, prompt_text: str, cwd: str, timeout_s: int,
    model=None, effort=None, skip_permissions=False, sandbox=True,
    container: bool = False,
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
    not re-interpret those characters.

    `container=True` (Phase 4, used only by build_docker_argv below) always
    skips the win32 wrapping branch and returns the bare Linux shape
    (`[cursor_path] + cli_args`), regardless of what `sys.platform` reports —
    this function still runs on the Windows host process that constructs the
    `docker run` argv, but the *inner* `agent ...` invocation it's building
    always targets the Linux container, never the host OS. Without this flag,
    a Windows host would incorrectly wrap the containerized invocation in a
    powershell.exe prefix that doesn't exist inside the container. Default
    False keeps every existing (native, non-containerized) call site and its
    tests unchanged."""
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
    if not container and sys.platform == "win32":
        return [_WINDOWS_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cursor_path] + cli_args
    return [cursor_path] + cli_args


def build_docker_argv(
    image: str, prompt_text: str, cwd: str, timeout_s: int, job_id: str,
    model=None, effort=None, skip_permissions=False, sandbox=True,
) -> list[str]:
    """Wraps the inner cursor invocation (build_argv, above, called with
    container=True) in `docker run`. Mirrors codex.py's build_docker_argv
    shape: --rm so a finished container never lingers; --name is
    deterministic from job_id so stop() (treekill.stop_container) can target
    it later. No --network none: cursor-agent needs outbound HTTPS to
    Cursor's own API.

    **Auth (live-verified 2026-07-27, OAuth device-flow login, same shape as
    agy's Phase 2 — NOT the CURSOR_API_KEY approach originally planned)**:
    `cadet-cursor-auth` (see CADET_CURSOR_AUTH_VOLUME) is a dedicated Docker
    named volume mounted at `/root/.config/cursor` — critically **not**
    `/root/.cursor` (the Windows install's config dir; confirmed empirically
    via a full-home-directory capture during a real login that cursor-agent's
    Linux build stores its actual session credential, `auth.json`, under the
    XDG config dir `~/.config/cursor/`, entirely separate from
    `~/.cursor/cli-config.json`, which holds non-secret settings only and is
    not required for auth). No cross-platform credential copy is possible —
    the Windows host's `~/.cursor` has no equivalent Linux XDG auth.json, so
    (like agy, unlike codex) a one-time interactive OAuth login must be done
    directly against this volume before any headless job can authenticate:
    `docker run --rm -v cadet-cursor-auth:/root/.config/cursor -e
    NO_OPEN_BROWSER=1 <image> agent login` — prints a `cursor.com/
    loginDeepControl?...` URL, blocks until the browser flow completes (no
    `-it`/TTY actually required, unlike agy's documented flow — confirmed
    working via a plain background subprocess). A real headless call
    (`agent -p "..." --print --mode plan`) and a real file-write call
    (`agent -p "..." --print --trust --force`) were both confirmed working
    end-to-end against this volume before this was wired up. `CURSOR_API_KEY`
    (`config.get_cursor_api_key()`) is still forwarded via `-e` when set, as
    an optional override the vendor CLI itself supports — but it is no
    longer required; the auth volume is the primary mechanism now, and there
    is no fail-fast RuntimeError on it being unset the way there was in the
    initial (unverified) API-key-only design.

    The `--mode plan` / `--trust --force` argv-level semantics (see
    build_argv's docstring, originally validated on Windows only) are now
    **confirmed identical inside this Linux container** via the live tests
    above. The /workspace bind mount's :ro/:rw distinction still backs the
    read-only/read-write semantics as defense-in-depth, same reasoning as
    codex's Phase 3, even though the CLI-level flags are now known to work
    correctly on their own too."""
    from cadet import config
    from cadet.process.launcher import container_name_for_job

    name = container_name_for_job("cursor", job_id)
    mount_suffix = ":ro" if (sandbox and not skip_permissions) else ""
    argv = [
        "docker", "run", "--rm", "--name", name,
        "-v", f"{cwd}:/workspace{mount_suffix}", "-w", "/workspace",
        "-v", f"{config.get_cursor_auth_volume()}:/root/.config/cursor",
        "--memory", config.get_cursor_container_memory(),
        "--cpus", config.get_cursor_container_cpus(),
        "--pids-limit", str(config.get_cursor_container_pids_limit()),
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ]
    api_key = config.get_cursor_api_key()
    if api_key:
        argv += ["-e", f"CURSOR_API_KEY={api_key}"]
    argv.append(image)
    argv += build_argv("agent", prompt_text, "/workspace", timeout_s, model, effort, skip_permissions, sandbox, container=True)
    return argv


async def spawn(
    image: str, prompt_text: str, cwd: str, timeout_s: int, stdout_fh, stderr_fh, job_id: str,
    model=None, effort=None, skip_permissions=False, sandbox=True,
):
    """Spawn the `docker run` client as a foreground subprocess for the
    containerized cursor provider (Phase 4) -- see launcher.spawn_agy's
    docstring for why this satisfies dispatcher.py's proc.pid/.wait()
    contract unchanged (the docker-run client's PID, resolving when the
    container exits). stdin=DEVNULL as always -- this process is a
    long-running MCP server's subprocess; never let a child inherit its
    stdio transport pipe.

    Breaking signature change from the native (pre-Phase-4) version: takes
    `image` instead of `cursor_path`, plus a new `job_id` param, mirroring
    codex.py's containerized spawn exactly -- same "no dual mode" decision
    already made for agy and codex."""
    argv = build_docker_argv(image, prompt_text, cwd, timeout_s, job_id, model, effort, skip_permissions, sandbox)
    return await asyncio.create_subprocess_exec(
        *argv, stdout=stdout_fh, stderr=stderr_fh, stdin=subprocess.DEVNULL,
    )


def stop(job_id: str, pid: int) -> None:
    """Provider-specific stop for cursor, mirroring codex.py's stop exactly:
    the recorded pid is the docker-run client's PID, not the container's --
    the container is a daemon-managed object with its own lifetime,
    independent of that client process still being alive. stop_container is
    the real stop; kill_process_tree(pid) afterward is a cheap, idempotent
    defensive fallback on the client process itself."""
    from cadet import config
    from cadet.process.launcher import container_name_for_job
    from cadet.process.treekill import kill_process_tree, stop_container
    stop_container(container_name_for_job("cursor", job_id), grace_s=config.get_cursor_stop_grace_s())
    kill_process_tree(pid)


def _parse_duration_to_seconds(duration_str: str):
    match = _DURATION_PATTERN.fullmatch(duration_str)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_error(stderr_tail: str, finished_at_iso: str):
    """Scan a failed job's stderr tail for a quota-exhaustion message.
    Returns (None, None) on no match, which is the expected/common case, not
    an error. Checks the confirmed real message first (no reset ETA in it,
    so quota_reset_at stays None), then falls back to the original unconfirmed
    guessed shape (which does carry a duration) in case some plan tier emits
    that instead — see the module docstring and the two pattern constants
    above for why these are cursor-specific, not shared with other providers."""
    stderr_tail = stderr_tail or ""
    if _QUOTA_NO_RESET_PATTERN.search(stderr_tail):
        return "quota_exhausted", None
    match = _QUOTA_PATTERN_WITH_RESET.search(stderr_tail)
    if not match:
        return None, None
    seconds = _parse_duration_to_seconds(match.group(1))
    if seconds is None:
        return None, None
    finished_dt = datetime.fromisoformat(finished_at_iso)
    reset_dt = finished_dt + timedelta(seconds=seconds)
    return "quota_exhausted", reset_dt.isoformat()


__all__ = ["NAME", "AGENT_ID", "DISPLAY_NAME", "build_argv", "build_docker_argv", "spawn", "stop", "parse_error"]
