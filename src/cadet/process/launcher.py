import asyncio
import os
import subprocess

from cadet import config


def docker_user_flags() -> list[str]:
    """POSIX hosts only: passes the host UID/GID into the container via -e
    HOST_UID/HOST_GID so entrypoint.sh can chown its auth volume and drop
    from root to that UID/GID (via setpriv) before exec'ing the real CLI --
    fixes --cap-drop=ALL leaving root unable to write bind-mounted
    /workspace files owned by the host user (CAP_DAC_OVERRIDE is stripped).
    --cap-add=CHOWN/SETUID/SETGID re-grants just enough capability for that
    one entrypoint-time chown-then-drop (chown of the auth volume needs
    CAP_CHOWN too -- confirmed empirically, "Operation not permitted"
    without it even with SETUID/SETGID present); the kernel clears the
    process's capability sets automatically once it crosses uid 0 ->
    non-zero (no keepcaps), so the CLI itself never actually runs with
    elevated caps -- net security posture is unchanged from plain
    --cap-drop=ALL.

    os.getuid is absent on Windows, which is the correct signal to skip
    this entirely: Docker Desktop's WSL2-backend bind mounts don't enforce
    the same strict POSIX DAC checks that trigger this bug on native
    Linux/WSL2, so Windows hosts never hit it and get the old (root,
    no-drop) container behavior unchanged."""
    if not hasattr(os, "getuid"):
        return []
    return [
        "--cap-add=CHOWN", "--cap-add=SETUID", "--cap-add=SETGID",
        "-e", f"HOST_UID={os.getuid()}", "-e", f"HOST_GID={os.getgid()}",
    ]


def container_name_for_job(provider: str, job_id: str) -> str:
    """job_id is always "job-" + uuid4().hex[:12] (see mcp/tools.py), which only
    ever contains [a-z0-9-] -- a valid Docker container name (^[a-zA-Z0-9][a-zA-Z0-9_.-]+$).
    provider is one of registry.names(), also always [a-z] -- shared by every
    containerized provider (agy, codex, ...) so each gets its own deterministic,
    collision-free container name."""
    return f"cadet-{provider}-{job_id}"


def _inner_agy_argv(
    prompt_text: str, timeout_s: int,
    model=None, effort=None, skip_permissions=False, sandbox=True,
) -> list[str]:
    """Pure argv construction for the agy CLI itself, run inside the container.
    Identical flag logic to CADET's pre-container agy invocation, except
    --add-dir always targets the container-side bind-mount path "/workspace",
    never the host cwd -- getting this wrong reproduces agy's own documented
    "silent write to the wrong place, false success" bug class.

    The read-only/read-write distinction skip_permissions and sandbox
    represent is enforced at the container level by launcher.build_argv's
    /workspace bind mount (:ro vs default :rw) -- kernel VFS enforcement."""
    argv = [
        "agy", "-p", prompt_text,
        "--add-dir", "/workspace",
        "--print-timeout", f"{timeout_s}s",
        "--mode", "accept-edits",
    ]
    if sandbox:
        argv.append("--sandbox")
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    if skip_permissions:
        argv.append("--dangerously-skip-permissions")
    return argv


def build_argv(
    image: str, prompt_text: str, cwd: str, timeout_s: int, job_id: str,
    model=None, effort=None, skip_permissions=False, sandbox=True,
) -> list[str]:
    """Wraps the inner agy invocation in `docker run`. --rm so a finished
    container never lingers; --name is deterministic from job_id so
    stop_agy (treekill.stop_container) can target it later without needing
    any extra state threaded through job_store. No --network flag: agy needs
    outbound HTTPS to Gemini's API, so Docker's default bridge network is
    left alone (never --network none).

    The read-only/read-write distinction skip_permissions and sandbox
    represent is enforced by the /workspace bind mount itself (:ro vs default
    :rw) -- kernel VFS enforcement."""
    name = container_name_for_job("agy", job_id)
    mount_suffix = ":ro" if (sandbox and not skip_permissions) else ""
    argv = [
        "docker", "run", "--rm", "--name", name,
        "-v", f"{cwd}:/workspace{mount_suffix}", "-w", "/workspace",
        "-v", f"{config.get_agy_gemini_volume()}:/root/.gemini",
        "--memory", config.get_agy_container_memory(),
        "--cpus", config.get_agy_container_cpus(),
        "--pids-limit", str(config.get_agy_container_pids_limit()),
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        *docker_user_flags(),
        image,
    ]
    argv += _inner_agy_argv(prompt_text, timeout_s, model, effort, skip_permissions, sandbox)
    return argv


async def spawn_agy(
    image: str, prompt_text: str, cwd: str, timeout_s: int, stdout_fh, stderr_fh, job_id: str,
    model=None, effort=None, skip_permissions=False, sandbox=True,
):
    """Spawn the `docker run` client as a foreground subprocess -- its stdout/
    stderr are the containerized agy's own stdout/stderr by default (no -d),
    so log capture is unchanged from the pre-container behavior. Its `.pid`
    is the docker-run client's PID (a real Windows PID), and `.wait()`
    resolves when the container exits, satisfying dispatcher.py's existing
    proc.pid/.wait() contract without any structural change there.

    stdin=DEVNULL fixes a latent gap vs. the other 3 providers (which already
    set this) -- agy's native (pre-container) spawn never set it."""
    argv = build_argv(image, prompt_text, cwd, timeout_s, job_id, model, effort, skip_permissions, sandbox)
    return await asyncio.create_subprocess_exec(
        *argv, stdout=stdout_fh, stderr=stderr_fh, stdin=subprocess.DEVNULL,
    )


def stop_agy(job_id: str, pid: int) -> None:
    """Provider-specific stop for agy: the recorded pid is the docker-run
    client's PID, not the container's -- the container is a daemon-managed
    object with its own lifetime, independent of that client process still
    being alive. stop_container is the real stop; kill_process_tree(pid)
    afterward is a cheap, idempotent defensive fallback on the client
    process itself (e.g. if the docker daemon is unreachable and `docker
    stop` can't do anything)."""
    from cadet.process.treekill import kill_process_tree, stop_container
    stop_container(container_name_for_job("agy", job_id), grace_s=config.get_agy_stop_grace_s())
    kill_process_tree(pid)
