"""Codex CLI provider. Empirically validated 2026-07-26 against the real
installed binary (codex 1.1.7-equivalent, Windows), then containerized
2026-07-27 (Phase 3, mirroring agy's Phase 2 pattern — see
docs/ARCHITECTURE.md's "Containerized codex execution" section):
- `codex exec "<prompt>" -C <cwd> -s workspace-write ...` silently no-ops on
  Windows — the sandbox's write-enforcement helper (codex-windows-sandbox-setup.exe)
  fails to launch, so exit code is 0 but no file is written. This — plus every
  other CADET provider's native Windows sandbox being broken or bypassable
  the same way (see agy's Phase 2 rollout writeup) — is exactly why codex now
  runs exclusively inside a Docker container instead (no dual mode), same
  decision already made for agy.
- `-s read-only` runs cleanly (vendor default; genuinely safe, confirmed via a
  real invocation, both natively on Windows and inside the Linux container).
- `--dangerously-bypass-approvals-and-sandbox` is the only flag confirmed to
  actually apply edits headlessly — mapped to CADET's existing
  `skip_permissions` knob, mirroring agy's `--dangerously-skip-permissions`.
- `codex exec` always attempts to read stdin ("Reading additional input from
  stdin...") even when a prompt is given as an argv arg. A live subprocess
  spawned from the long-running MCP server would otherwise inherit the
  server's own stdio transport pipe as its stdin — spawn() explicitly
  redirects stdin to DEVNULL to guarantee immediate EOF instead.
- `-m/--model` and `-c model_reasoning_effort=<value>` both confirmed to parse
  and take effect via a real invocation.
- **Critical containerization finding — codex's Windows `~/.codex/auth.json`
  is DIRECTLY PORTABLE to the Linux container, no re-auth needed** (the
  OPPOSITE of what agy's Phase 2 found). Confirmed empirically: a scratch
  Docker volume seeded with just the host's `auth.json` (chatgpt OAuth mode,
  4.3KB) authenticated a real `codex exec` model call inside
  `cadet-codex:latest` with zero interactive login step. `config.toml` was
  A/B tested and confirmed NOT required for auth+basic exec. A **read-only**
  bind-mount of `~/.codex` does NOT work (codex writes to its own config dir
  even during a plain `exec`, e.g. PATH-alias bookkeeping — fails with
  "Read-only file system"); the auth volume must be writable, same as agy's.
- **Live-tested 2026-07-27 (first post-commit smoke test): codex's own internal exec sandbox
  (bubblewrap-based, gates BOTH `-s read-only` and `-s workspace-write`) cannot run a single command
  in this container** — not a missing-package gap, empirically isolated to this Docker Desktop/WSL2
  host blocking unprivileged user-namespace creation for every container, regardless of CADET's own
  `--cap-drop`/`--security-opt` flags (even a bare unrestricted `docker run` fails identically).
  `build_docker_argv` now always runs the inner codex process with
  `--dangerously-bypass-approvals-and-sandbox` and enforces the read-only/read-write distinction via
  the `/workspace` bind mount itself (`:ro` vs default `:rw`) instead — kernel VFS enforcement, not
  subject to the same restriction.
- **Confirmed 2026-07-27** (real free-tier/plan account, hit during live multi-provider
  stress-testing): the real quota-exhaustion message is `"You've hit your usage limit. Upgrade to
  Plus to continue using Codex (https://chatgpt.com/explore/plus), or try again at Aug 25th, 2026
  4:25 PM."` — exit code 1, WITH a reset ETA, but as an **absolute date/time**, not a compact
  duration like `5h30m`. **Critically, this error arrives as a `type: "error"`/`"turn.failed"`
  event inside the `--json` event stream on STDOUT, not on stderr** — stderr for this same failed
  job contained only the benign, always-present `"Reading additional input from stdin..."` line.
  This means no stderr-only regex, however correct, could ever have caught it; `parse_error` now
  accepts an optional `stdout_tail` and scans both. `_QUOTA_PATTERN_HIT_LIMIT` matches the phrase
  and `_ABSOLUTE_RESET_PATTERN` opportunistically extracts the date/time (parsed via
  `datetime.strptime`, ordinal suffix stripped by the regex itself); if the date portion doesn't
  parse for some reason, `quota_exhausted` is still returned with `quota_reset_at=None` rather than
  silently missing the classification entirely. The original guessed pattern
  (`_QUOTA_PATTERN`, `"usage limit reached...try again in <duration>"`) is kept as a fallback for a
  hypothetical different message shape, same reasoning as cursor's fix in commit `667b1f8`.
"""
import asyncio
import re
import subprocess
from datetime import datetime, timedelta

NAME = "codex"
AGENT_ID = "codex"
DISPLAY_NAME = "Codex CLI"

# _QUOTA_PATTERN is the original best-effort guess (never observed during
# initial validation) -- kept as a fallback shape in case some plan tier
# emits a compact duration instead. _QUOTA_PATTERN_HIT_LIMIT/
# _ABSOLUTE_RESET_PATTERN are the real, empirically confirmed shape
# (2026-07-27): "You've hit your usage limit ... or try again at Aug 25th,
# 2026 4:25 PM." -- an absolute date/time, not a compact duration, and
# delivered on stdout (codex's --json event stream), not stderr -- see the
# module docstring.
_QUOTA_PATTERN = re.compile(r"usage limit reached.*?(?:try again|resets?) (?:in |at )?([0-9dhms]+)", re.IGNORECASE | re.DOTALL)
_QUOTA_PATTERN_HIT_LIMIT = re.compile(r"hit your usage limit", re.IGNORECASE)
_ABSOLUTE_RESET_PATTERN = re.compile(
    r"try again at ([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*([AP]M)",
    re.IGNORECASE,
)
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


def build_docker_argv(
    image: str, prompt_text: str, cwd: str, timeout_s: int, job_id: str,
    model=None, effort=None, skip_permissions=False, sandbox=True,
) -> list[str]:
    """Wraps the inner codex invocation (build_argv, above) in `docker run`.
    Mirrors launcher.build_argv's agy wrapping shape exactly: --rm so a
    finished container never lingers; --name is deterministic from job_id so
    stop() (treekill.stop_container) can target it later without needing any
    extra state threaded through job_store. No --network flag: codex needs
    outbound HTTPS to the ChatGPT/OpenAI API, so Docker's default bridge
    network is left alone (never --network none). --add-dir has no codex
    equivalent -- `-C /workspace` (inside build_argv's own cwd param) is
    already the container-side path, never the host cwd, same "never let the
    inner CLI see a host path" rule as agy's --add-dir.

    codex's own internal exec sandbox (bubblewrap-based, gates BOTH
    `-s read-only` and `-s workspace-write` -- not just the write path) cannot
    run in this container: empirically confirmed the failure is unprivileged
    user-namespace creation being blocked at the Docker/host level for every
    container regardless of CADET's own --cap-drop/--security-opt flags, not
    a missing bubblewrap package (live-tested 2026-07-27, see
    ARCHITECTURE.md's "Validated `codex` container behavior"). So the inner
    codex process here always runs with
    --dangerously-bypass-approvals-and-sandbox -- CADET's own --cap-drop=ALL /
    --security-opt=no-new-privileges / --rm / per-job container is the real
    security boundary, same reasoning as the native-Windows path's
    abandonment. The read-only/read-write distinction skip_permissions and
    sandbox represent is instead enforced by the /workspace bind mount itself
    (:ro vs default :rw) -- kernel VFS enforcement, not subject to the same
    userns restriction."""
    from cadet import config
    from cadet.process.launcher import container_name_for_job

    name = container_name_for_job("codex", job_id)
    mount_suffix = ":ro" if (sandbox and not skip_permissions) else ""
    argv = [
        "docker", "run", "--rm", "--name", name,
        "-v", f"{cwd}:/workspace{mount_suffix}", "-w", "/workspace",
        "-v", f"{config.get_codex_auth_volume()}:/root/.codex",
        "--memory", config.get_codex_container_memory(),
        "--cpus", config.get_codex_container_cpus(),
        "--pids-limit", str(config.get_codex_container_pids_limit()),
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        image,
    ]
    argv += build_argv("codex", prompt_text, "/workspace", timeout_s, model, effort, skip_permissions=True, sandbox=sandbox)
    return argv


async def spawn(
    image: str, prompt_text: str, cwd: str, timeout_s: int, stdout_fh, stderr_fh, job_id: str,
    model=None, effort=None, skip_permissions=False, sandbox=True,
):
    """Spawn the `docker run` client as a foreground subprocess for the
    containerized codex provider -- see launcher.spawn_agy's docstring for
    why this satisfies dispatcher.py's proc.pid/.wait() contract unchanged
    (the docker-run client's PID, resolving when the container exits).
    stdin=DEVNULL as always -- this process is a long-running MCP server's
    subprocess; never let a child inherit its stdio transport pipe."""
    argv = build_docker_argv(image, prompt_text, cwd, timeout_s, job_id, model, effort, skip_permissions, sandbox)
    return await asyncio.create_subprocess_exec(
        *argv, stdout=stdout_fh, stderr=stderr_fh, stdin=subprocess.DEVNULL,
    )


def stop(job_id: str, pid: int) -> None:
    """Provider-specific stop for codex, mirroring launcher.stop_agy exactly:
    the recorded pid is the docker-run client's PID, not the container's --
    the container is a daemon-managed object with its own lifetime,
    independent of that client process still being alive. stop_container is
    the real stop; kill_process_tree(pid) afterward is a cheap, idempotent
    defensive fallback on the client process itself."""
    from cadet import config
    from cadet.process.launcher import container_name_for_job
    from cadet.process.treekill import kill_process_tree, stop_container
    stop_container(container_name_for_job("codex", job_id), grace_s=config.get_codex_stop_grace_s())
    kill_process_tree(pid)


def _parse_duration_to_seconds(duration_str: str):
    match = _DURATION_PATTERN.fullmatch(duration_str)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _parse_absolute_reset(month: str, day: str, year: str, hour: str, minute: str, meridiem: str):
    text = f"{month} {day} {year} {hour}:{minute} {meridiem.upper()}"
    for fmt in ("%b %d %Y %I:%M %p", "%B %d %Y %I:%M %p"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_error(stderr_tail: str, finished_at_iso: str, stdout_tail: str = ""):
    """Scan a failed job's stderr AND stdout tails for a quota-exhaustion
    message. Returns (None, None) on no match, which is the expected/common
    case, not an error. `stdout_tail` is required here (unlike the other
    providers, where it's defensive) because codex's real quota error is
    delivered via its `--json` event stream on stdout, not stderr — see the
    module docstring. Checks the confirmed real "hit your usage limit"
    phrasing first (opportunistically extracting an absolute-date reset ETA
    if present and parseable), then falls back to the original unconfirmed
    guessed shape (which expects a compact duration) in case some other plan
    tier emits that instead."""
    combined = f"{stderr_tail or ''}\n{stdout_tail or ''}"
    if _QUOTA_PATTERN_HIT_LIMIT.search(combined):
        match = _ABSOLUTE_RESET_PATTERN.search(combined)
        if match:
            reset_dt = _parse_absolute_reset(*match.groups())
            if reset_dt is not None:
                return "quota_exhausted", reset_dt.isoformat()
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
