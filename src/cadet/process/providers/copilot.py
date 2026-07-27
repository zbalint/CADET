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
- **Confirmed 2026-07-27** (real free-tier account, hit twice during live
  multi-provider stress-testing via real `delegate_task` calls): the real
  quota-exhaustion message is `"You have exceeded your monthly quota (Request
  ID: <id>)."`, exit code 1, delivered on **stderr** (this provider always
  uses `--output-format text`, not a JSON event stream, unlike codex — so
  the existing stderr-only scan already reaches it), **no reset-time/ETA
  anywhere in the message**. `parse_error` matches this via
  `_QUOTA_NO_RESET_PATTERN` and returns `("quota_exhausted", None)`. The
  original guessed pattern (`_QUOTA_PATTERN`) is kept as a fallback in case a
  different plan tier emits reset-ETA wording. Two real non-quota error
  strings were also captured during earlier validation (the model/effort
  mismatch and the model-not-available error) and remain covered by
  parse_error's tests as confirmed non-matches.

Containerized 2026-07-27 (Phase 5, mirroring codex's Phase 3 / cursor's
Phase 4 pattern — see docs/ARCHITECTURE.md's "Containerized copilot
execution" section). Fully replaces the native Windows node.exe+npm-loader.js
path above — no dual mode, same decision already made for agy, codex, and
cursor:
- Unlike codex/cursor (a single static Linux binary), `@github/copilot` is an
  npm package with per-platform `optionalDependencies`
  (`@github/copilot-linux-x64` etc., resolved automatically by `npm install`
  at build time) — hence the `node:22-bookworm-slim` base image rather than a
  raw binary download onto `debian:bookworm-slim`. Inside the container the
  globally-installed `copilot` binary is directly on PATH, so no
  node.exe/npm-loader.js indirection is needed the way the native Windows
  npm-global-install layout requires (see `_resolve_windows_invocation`
  above) — `build_argv(container=True)` skips straight to `[copilot_path]`.
- **Auth: CONFIRMED live 2026-07-27, via `docker run -it` (a real allocated
  pseudo-TTY), NOT the plain `copilot_docker_setup.py login()` subprocess
  path (`docker run` without `-it`).** Two attempts through
  `copilot_docker_setup.py`'s non-interactive `subprocess.run(["docker",
  "run", "--rm", ...])` (no `-it`) both completed the OAuth device-flow
  successfully but then printed "Login succeeded, but the token was not
  saved. Install a system keychain or rerun login and accept plaintext
  storage." and exited 1 — this minimal container has no system keychain
  (no D-Bus/libsecret/gnome-keyring), and the plain-text fallback
  `copilot login --help` documents does NOT auto-trigger without a real
  TTY; piping answers via stdin (`docker run -i` without `-t`) made no
  difference either. The fix, confirmed working: run `docker run --rm -it
  -v cadet-copilot-auth:/root/.copilot cadet-copilot:latest copilot login`
  directly in a real interactive terminal (this cannot be done through an
  automation harness with no PTY — the user ran it themselves, mirroring
  agy's Phase 2 interactive-login requirement exactly), accept the
  plaintext-storage prompt when asked, and the resulting
  `/root/.copilot/config.json` (holding the real token, not just the
  non-secret `loggedInUsers` metadata the Windows host's copy has) persists
  in the volume. Verified via a real subsequent authenticated call (`docker
  run --rm -v cadet-copilot-auth:/root/.copilot cadet-copilot:latest
  copilot -p "What is 2+2?" --allow-all-tools --model auto ...` →
  "2+2 equals 4.", zero further login needed). **`copilot_docker_setup.py`'s
  own `login()` should be treated as non-functional as currently written**
  (no `-it`) — either fix it to allocate a real TTY (unclear how to do that
  from a non-interactive Python `subprocess.run` at all) or keep documenting
  the manual `docker run -it` command as the real setup step.
  1. The `cadet-copilot-auth` Docker volume mounted at `/root/.copilot` (the
     CLI's own default `COPILOT_HOME`) is the primary mechanism, populated
     as described above.
  2. `COPILOT_GITHUB_TOKEN` (`config.get_copilot_github_token()`,
     `CADET_COPILOT_GITHUB_TOKEN` env var), forwarded via `-e` when set —
     confirmed by `copilot help environment`/`copilot login --help` (real
     vendor docs, not yet exercised end-to-end with a real token) to take
     precedence over any stored credential. A fine-grained PAT with the
     "Copilot Requests" permission (note: only appears in GitHub's UI when
     the token's Resource owner is your personal account, not an
     organization — a real gotcha hit while investigating this) or a `gh`
     CLI OAuth token both work per the same docs; classic PATs (`ghp_`) are
     explicitly not supported. Needs no interactive device-flow step at all
     if you have a token handy, unlike mechanism 1 above.
- The `--mode plan --deny-tool shell` / `--allow-all-tools` argv-level
  semantics validated above (on native Windows) are assumed, NOT yet
  confirmed, to hold identically inside the Linux container — no live
  container run has exercised them yet (unlike cursor's Phase 4, which did
  confirm this before wiring up). The `/workspace` bind mount's `:ro`/`:rw`
  distinction still backs the read-only/read-write semantics as
  defense-in-depth regardless.
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

# _QUOTA_PATTERN is the original best-effort guess (never observed during
# initial validation) -- kept as a fallback shape in case some plan tier
# emits it instead. _QUOTA_NO_RESET_PATTERN is the real, empirically
# confirmed message (2026-07-27, real free-tier account, live job failure
# via delegate_task): "You have exceeded your monthly quota (Request ID:
# ...)." -- exit code 1, delivered on stderr, no reset-time/ETA anywhere in
# the message, so quota_reset_at legitimately stays None for this shape.
# Same "provider-specific, never shared" reasoning as cursor's fix in commit
# 667b1f8 -- do NOT unify this with agy/codex/cursor's own patterns.
_QUOTA_PATTERN = re.compile(r"usage limit reached.*?(?:try again|resets?) (?:in |at )?([0-9dhms]+)", re.IGNORECASE | re.DOTALL)
_QUOTA_NO_RESET_PATTERN = re.compile(r"exceeded your monthly quota", re.IGNORECASE)
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
    container: bool = False,
) -> list[str]:
    """Pure argv construction. `copilot` has no CLI-level timeout flag —
    CADET's own asyncio.wait_for + kill_process_tree already enforces
    timeout_s independently, same as the other providers.

    On Windows, `copilot_path`'s own `.cmd`/`.ps1` are both bypassed (see
    module docstring for the two separate, unrelated bugs each has) in favor
    of invoking `node.exe` + the vendor's own `npm-loader.js` directly.

    `container=True` (Phase 5, used only by build_docker_argv below) always
    skips the win32 node.exe+npm-loader.js resolution branch and returns the
    bare Linux shape (`[copilot_path] + cli_args`), regardless of what
    `sys.platform` reports — this function still runs on the Windows host
    process that constructs the `docker run` argv, but the *inner*
    `copilot ...` invocation it's building always targets the Linux
    container, where the globally-installed `copilot` binary is already
    directly on PATH (no node.exe/npm-loader.js indirection needed, unlike
    the native Windows npm-global-install layout). Default False keeps every
    existing (native, non-containerized) call site and its tests unchanged."""
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
    if not container and sys.platform == "win32":
        return _resolve_windows_invocation(copilot_path) + cli_args
    return [copilot_path] + cli_args


def build_docker_argv(
    image: str, prompt_text: str, cwd: str, timeout_s: int, job_id: str,
    model=None, effort=None, skip_permissions=False, sandbox=True,
) -> list[str]:
    """Wraps the inner copilot invocation (build_argv, above, called with
    container=True) in `docker run`. Mirrors codex.py's/cursor.py's
    build_docker_argv shape: --rm so a finished container never lingers;
    --name is deterministic from job_id so stop() (treekill.stop_container)
    can target it later. No --network none: copilot needs outbound HTTPS to
    GitHub's Copilot API.

    **Auth (Phase 5, UNCONFIRMED against a real live login — see module
    docstring and copilot_docker_setup.py for the same caveat)**: two
    mechanisms, mirroring cursor's "auth volume primary, key/token optional
    override" shape:
    1. `cadet-copilot-auth` (config.get_copilot_auth_volume()) is a Docker
       named volume mounted at `/root/.copilot` (the CLI's own default
       `COPILOT_HOME`). A one-time `copilot login` device-flow against this
       volume (see copilot_docker_setup.py) is intended to populate it.
    2. `COPILOT_GITHUB_TOKEN` (config.get_copilot_github_token(),
       `CADET_COPILOT_GITHUB_TOKEN` env var) is forwarded via `-e` when set —
       per `copilot login --help`/`copilot help environment`, this takes
       precedence over any stored credential, mirroring CURSOR_API_KEY's
       optional-override relationship to cursor's own auth volume."""
    from cadet import config
    from cadet.process.launcher import container_name_for_job

    name = container_name_for_job("copilot", job_id)
    mount_suffix = ":ro" if (sandbox and not skip_permissions) else ""
    argv = [
        "docker", "run", "--rm", "--name", name,
        "-v", f"{cwd}:/workspace{mount_suffix}", "-w", "/workspace",
        "-v", f"{config.get_copilot_auth_volume()}:/root/.copilot",
        "--memory", config.get_copilot_container_memory(),
        "--cpus", config.get_copilot_container_cpus(),
        "--pids-limit", str(config.get_copilot_container_pids_limit()),
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ]
    token = config.get_copilot_github_token()
    if token:
        argv += ["-e", f"COPILOT_GITHUB_TOKEN={token}"]
    argv.append(image)
    argv += build_argv("copilot", prompt_text, "/workspace", timeout_s, model, effort, skip_permissions, sandbox, container=True)
    return argv


async def spawn(
    image: str, prompt_text: str, cwd: str, timeout_s: int, stdout_fh, stderr_fh, job_id: str,
    model=None, effort=None, skip_permissions=False, sandbox=True,
):
    """Spawn the `docker run` client as a foreground subprocess for the
    containerized copilot provider (Phase 5) -- see launcher.spawn_agy's
    docstring for why this satisfies dispatcher.py's proc.pid/.wait()
    contract unchanged (the docker-run client's PID, resolving when the
    container exits). stdin=DEVNULL as always -- this process is a
    long-running MCP server's subprocess; never let a child inherit its
    stdio transport pipe.

    Breaking signature change from the native (pre-Phase-5) version: takes
    `image` instead of `copilot_path`, plus a new `job_id` param, mirroring
    codex.py's/cursor.py's containerized spawn exactly -- same "no dual mode"
    decision already made for agy, codex, and cursor."""
    argv = build_docker_argv(image, prompt_text, cwd, timeout_s, job_id, model, effort, skip_permissions, sandbox)
    return await asyncio.create_subprocess_exec(
        *argv, stdout=stdout_fh, stderr=stderr_fh, stdin=subprocess.DEVNULL,
    )


def stop(job_id: str, pid: int) -> None:
    """Provider-specific stop for copilot, mirroring codex.py's/cursor.py's
    stop exactly: the recorded pid is the docker-run client's PID, not the
    container's -- the container is a daemon-managed object with its own
    lifetime, independent of that client process still being alive.
    stop_container is the real stop; kill_process_tree(pid) afterward is a
    cheap, idempotent defensive fallback on the client process itself."""
    from cadet import config
    from cadet.process.launcher import container_name_for_job
    from cadet.process.treekill import kill_process_tree, stop_container
    stop_container(container_name_for_job("copilot", job_id), grace_s=config.get_copilot_stop_grace_s())
    kill_process_tree(pid)


def _parse_duration_to_seconds(duration_str: str):
    match = _DURATION_PATTERN.fullmatch(duration_str)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_error(stderr_tail: str, finished_at_iso: str, stdout_tail: str = ""):
    """Scan a failed job's stderr (and, defensively, stdout) tail for a
    quota-exhaustion message. Returns (None, None) on no match, which is the
    expected/common case, not an error. Checks the confirmed real message
    first (no reset ETA in it, so quota_reset_at stays None), then falls
    back to the original unconfirmed guessed shape (which does carry a
    duration) in case some plan tier emits that instead — see the module
    docstring and the two pattern constants above for why these are
    copilot-specific, not shared with other providers."""
    combined = f"{stderr_tail or ''}\n{stdout_tail or ''}"
    if _QUOTA_NO_RESET_PATTERN.search(combined):
        return "quota_exhausted", None
    match = _QUOTA_PATTERN.search(combined)
    if not match:
        return None, None
    seconds = _parse_duration_to_seconds(match.group(1))
    if seconds is None:
        return None, None
    finished_dt = datetime.fromisoformat(finished_at_iso)
    reset_dt = finished_dt + timedelta(seconds=seconds)
    return "quota_exhausted", reset_dt.isoformat()


__all__ = ["NAME", "AGENT_ID", "DISPLAY_NAME", "build_argv", "build_docker_argv", "spawn", "stop", "parse_error"]
