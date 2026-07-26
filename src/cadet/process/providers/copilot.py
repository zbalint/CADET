"""GitHub Copilot CLI provider. Empirically validated 2026-07-26 against the
real installed binary (`@github/copilot` npm package v1.0.75, Windows):

- **Critical — on Windows, `copilot_path` (expected to be the vendor's own
  `copilot.cmd` shim) is never exec'd directly, and neither is its `.ps1`
  sibling.** Both are broken, for two DIFFERENT reasons:
  - `copilot.cmd` is a `cmd.exe` batch wrapper (`... %*`) — same class of bug
    already found for `cursor.cmd`: exec'ing it directly routes through
    `cmd.exe`, which re-parses `<`/`>`/`&`/`|`/`^` before `%*` is
    substituted. Reproduced: a prompt containing CADET's own rendered-prompt
    placeholder (`<one-line summary + outcome>`) made the `.cmd` fail with
    `The system cannot find the file specified.` (`cmd.exe` treating it as
    an input redirection).
  - **`copilot.ps1` looked like the fix (mirroring `cursor`'s), but is
    ALSO broken — for an unrelated reason.** `copilot.ps1` internally does
    `& "$basedir/node.exe" ".../npm-loader.js" $args` — a SECOND, internal
    native-command invocation. When `copilot.ps1` itself is launched fresh
    via `powershell.exe -File` (exactly the mechanism CADET/cursor use,
    non-interactively, not from an already-running interactive PowerShell
    session), PowerShell's own serialization of a `$args` element containing
    an embedded literal double-quote followed eventually by a space (e.g.
    `content="<one-line summary + outcome>"` — quote, words with a space,
    quote) gets corrupted during that internal re-invocation. Node then
    receives extra split-apart positional arguments and copilot's own CLI
    (commander.js) emits `error: Invalid command format. It looks like your
    prompt was not quoted...` — a real, reproducible false positive, not a
    cmd.exe redirection issue. Confirmed by minimal repro: a plain
    `powershell.exe -File <script> -p "<value>"` correctly preserves an
    argument containing `<`/`>` end-to-end (proven with a diagnostic script
    that just echoes `$args`) — so `-File`'s own tokenizing is NOT at fault.
    The corruption is specifically in `copilot.ps1`'s own internal
    `& node.exe ... $args` hop, which cannot be patched (it's vendor code
    that changes on every `copilot update`).
  - **The fix: invoke `node.exe` and the vendor's own
    `node_modules/@github/copilot/npm-loader.js` directly**, skipping BOTH
    `copilot.cmd` and `copilot.ps1` (and therefore both bugs) entirely.
    `npm-loader.js` is a thin, stable loader (`spawnSync(realBinary,
    process.argv.slice(2), {stdio:"inherit"})`, no shell involved) that
    resolves whatever platform binary is currently installed under the same
    `node_modules/@github/copilot/` tree — so, like pointing `cursor` at its
    `.ps1` instead of a hardcoded versioned path, this still survives
    `npm install -g @github/copilot` version bumps. Confirmed via two direct
    repro calls (`node.exe npm-loader.js -p "<the exact failing prompt>"
    ...`): both the plan-mode text case and a real file edit (content
    containing both `<...>` and an embedded `"quoted phrase"`) succeeded
    with byte-for-byte correct output, with neither of the two `.cmd`/`.ps1`
    bugs reproducing.
  - `node.exe`'s location is resolved as: a sidecar `node.exe` next to
    `copilot_path` if present (mirrors the vendor shim's own preferred
    lookup, e.g. some Node distributions do bundle one there), else the
    `CADET_COPILOT_NODE_PATH` env var, which must point at an existing file.
    **No silent bare `"node"` PATH lookup** — same reasoning CADET already
    applies to `CADET_AGY_PATH`: an MCP-launched server process's PATH isn't
    reliably the same as an interactive shell's, and on this actual
    validation machine, no sidecar `node.exe` existed next to the npm
    global shims (it was only at `C:\\Program Files\\nodejs\\node.exe`), so
    the explicit-env-var path is the one that matters in practice, not just
    a defensive fallback for a hypothetical.
- **`-C <cwd>` is a real, confirmed flag** — Phase 0's research had flagged
  this as unconfirmed/absent — it exists and works.
- **`--allow-all-tools` is required for reliable non-interactive (`-p`)
  operation, confirmed both ways: with it, `-C`/`-p`/`--mode plan` all work
  cleanly and return promptly; without it (and without `--mode plan`
  either), a real headless invocation asking for a file write took >20s and
  never completed the edit (matches the vendor's own `--help` text: "required
  for non-interactive mode") — same "known non-functional combo" class as
  codex's broken workspace-write and cursor's `--force`-without-`--trust`.
  **Always passed unconditionally**, same reasoning as cursor's unconditional
  `--trust`.
- **`--mode plan` alone is NOT a reliable write gate — it must be paired
  with `--deny-tool shell`.** A short, direct plan-mode test (`--mode plan
  --allow-all-tools`, trivial prompt) showed zero file writes, looking like
  a clean safe/read-only lever mirroring cursor's `--mode plan`. But a real
  end-to-end run through CADET's actual rendered prompt template — which
  tells the model to "proceed autonomously using your best judgement" —
  showed the model explicitly notice its dedicated edit tool was
  plan-mode-restricted, then deliberately invoke the shell tool (`--allow-
  all-tools` grants that too) to write the file anyway, narrating exactly
  this reasoning in its own output. Confirmed via a clean A/B with a prompt
  explicitly nudging toward a shell workaround: `--mode plan --allow-all-
  tools` alone let the write through via shell; adding `--deny-tool shell`
  blocked it (model correctly reported it could not proceed) while a
  separate real call confirmed read-only exploration (listing directory
  contents) still worked fine without the shell tool. `providers/
  copilot.py` therefore always pairs `--mode plan` with `--deny-tool shell`.
- Because `--allow-all-tools` is unconditional, the actual write/no-write
  gate is `--mode plan --deny-tool shell`'s presence: `skip_permissions=True`
  omits both (real edits, confirmed via multiple separate real file writes);
  `sandbox=True` (default) adds both. Unlike codex/cursor, copilot's
  `sandbox=False, skip_permissions=False` combo is NOT a known-broken state
  — it behaves identically to `skip_permissions=True` (neither flag present
  either way) because `--allow-all-tools` alone is what makes non-
  interactive mode function at all, and `--mode plan --deny-tool shell` is
  the only mechanism that restricts
  it. This is a genuine platform difference, not an oversight: copilot has
  no distinct "unsandboxed but still asks permission" state in
  non-interactive mode.
- **`--model` is always passed explicitly** (default `"auto"`), mirroring
  cursor's defensive stance — not empirically confirmed to have cursor's
  sticky-global-default bug, but passing explicitly costs nothing and avoids
  relying on unconfirmed default-resolution behavior.
- **`--effort`/`--reasoning-effort`** (choices: none, minimal, low, medium,
  high, xhigh, max) is a real, confirmed flag — but confirmed to hard-error
  when combined with `model="auto"`: a real call with `--model auto --effort
  minimal` failed with `Error: Model "auto" does not support reasoning
  effort configuration (requested: "minimal").`. Only applied when `effort`
  is set; callers must pair it with a real (non-auto) model, or the job will
  fail with that vendor error — documented, not defended against here (same
  as CADET deferring model/effort validity to the provider's own error
  surface elsewhere). No specific named model was confirmed available on
  this account (`gpt-5.1`, `claude-sonnet-4.5`, `claude-sonnet-4`, `gpt-5`,
  `gpt-5-mini`, `gpt-4.1` all returned "not available") — an account/plan
  limitation, not a CADET bug.
- `--silent` (stats-free output) and `--no-color` are always passed to keep
  the job's stdout log clean; `--output-format text` is the vendor default
  and kept explicit for clarity, matching cursor's style.
- stdin is still explicitly redirected to DEVNULL as a defensive measure,
  same as every other provider (this process is a long-running MCP server's
  subprocess; never let a child inherit its stdio transport pipe).
- No structured quota-exhaustion signal was found in vendor docs or during
  validation (this account never hit a real rate limit) — parse_error is
  best-effort only, same posture as codex's/cursor's unconfirmed regex. Two
  real non-quota error strings were captured during validation (the
  model/effort mismatch and the model-not-available error) and are covered
  by parse_error's tests as confirmed non-matches.
"""
import asyncio
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

NAME = "copilot"
AGENT_ID = "copilot"
DISPLAY_NAME = "GitHub Copilot CLI"

# Best-effort only: not empirically confirmed (never observed a real quota
# exhaustion during validation, and forcing one would burn the pool). Not
# finding a match is not an error, same as agy/codex/cursor's quota parsers.
_QUOTA_PATTERN = re.compile(r"usage limit reached.*?(?:try again|resets?) (?:in |at )?([0-9dhms]+)", re.IGNORECASE | re.DOTALL)
_DURATION_PATTERN = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def _resolve_windows_invocation(copilot_path: str) -> list[str]:
    """Returns [node_exe, loader_path] — see module docstring for why
    `copilot_path` (expected to be `copilot.cmd`) is never exec'd directly
    on Windows. `loader_path` is derived relative to `copilot_path`'s own
    directory, matching npm's standard global-install layout (same
    directory as the `copilot`/`copilot.cmd`/`copilot.ps1` shims)."""
    basedir = os.path.dirname(copilot_path)
    node_exe = os.path.join(basedir, "node.exe")
    if not os.path.isfile(node_exe):
        node_exe = os.environ.get("CADET_COPILOT_NODE_PATH")
        if not node_exe or not os.path.isfile(node_exe):
            raise RuntimeError(
                f"Could not resolve node.exe for the copilot provider: no node.exe next to "
                f"{copilot_path!r}, and CADET_COPILOT_NODE_PATH is not set to an existing file. "
                "Set CADET_COPILOT_NODE_PATH to your Node.js installation's node.exe."
            )
    loader_path = os.path.join(basedir, "node_modules", "@github", "copilot", "npm-loader.js")
    if not os.path.isfile(loader_path):
        raise RuntimeError(f"copilot's npm-loader.js not found at expected path: {loader_path!r}")
    return [node_exe, loader_path]


def build_argv(
    copilot_path: str, prompt_text: str, cwd: str, timeout_s: int,
    model=None, effort=None, skip_permissions=False, sandbox=True,
) -> list[str]:
    """Pure argv construction. `copilot` has no CLI-level timeout flag —
    CADET's own asyncio.wait_for + kill_process_tree already enforces
    timeout_s independently, same as the other providers.

    On Windows, `copilot_path`'s own `.cmd`/`.ps1` are both bypassed (see
    module docstring for the two separate, unrelated bugs each has) in favor
    of invoking `node.exe` + the vendor's own `npm-loader.js` directly."""
    model_arg = model or "auto"
    cli_args = [
        "-p", prompt_text,
        "-C", cwd,
        "--output-format", "text",
        "--model", model_arg,
        "--allow-all-tools",
        "--silent",
        "--no-color",
    ]
    if effort:
        cli_args += ["--effort", effort]
    if not skip_permissions and sandbox:
        # --mode plan alone is NOT sufficient: confirmed via a real run with
        # CADET's actual rendered prompt (which tells the model to "proceed
        # autonomously using your best judgement") that the model will use
        # the shell tool to write a file as a deliberate workaround once it
        # notices its dedicated edit tool is unavailable in plan mode — see
        # module docstring. --deny-tool shell closes that gap; confirmed via
        # a real A/B that it blocks the write-via-shell workaround while
        # still allowing read-only exploration (e.g. listing directory
        # contents) to keep working.
        cli_args += ["--mode", "plan", "--deny-tool", "shell"]
    # skip_permissions=True, or sandbox=False with skip_permissions=False,
    # both omit --mode plan and therefore both allow real edits — see module
    # docstring for why this collapse (unlike codex/cursor) is a genuine
    # platform behavior, not a bug being papered over.
    if sys.platform == "win32":
        return _resolve_windows_invocation(copilot_path) + cli_args
    return [copilot_path] + cli_args


async def spawn(
    copilot_path: str, prompt_text: str, cwd: str, timeout_s: int, stdout_fh, stderr_fh,
    model=None, effort=None, skip_permissions=False, sandbox=True,
):
    """Spawn copilot (via `node.exe npm-loader.js` on Windows, see
    build_argv) as a background subprocess. stdin is explicitly DEVNULL as a
    defensive measure (see module docstring)."""
    argv = build_argv(copilot_path, prompt_text, cwd, timeout_s, model, effort, skip_permissions, sandbox)
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
    message. UNCONFIRMED — no real copilot rate-limit wording was observed
    during validation. Returns (None, None) on no match, which is the
    expected/common case, not an error."""
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
