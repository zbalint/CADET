import asyncio
import subprocess
import sys


def build_argv(
    agy_path: str, prompt_text: str, cwd: str, timeout_s: int,
    model=None, effort=None, skip_permissions=False, sandbox=True,
) -> list[str]:
    """Pure argv construction, kept separate from the actual subprocess call so
    it's unit-testable without touching asyncio. Order matches JOB_LIFECYCLE.md's
    "Process management" section exactly."""
    argv = [
        agy_path, "-p", prompt_text,
        "--add-dir", cwd,
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


async def spawn_agy(
    agy_path: str, prompt_text: str, cwd: str, timeout_s: int, stdout_fh, stderr_fh,
    model=None, effort=None, skip_permissions=False, sandbox=True,
):
    """Spawn `agy` as a background subprocess. No shell=True — argv list avoids
    shell quoting/escaping issues on the rendered prompt entirely. Uses
    CREATE_NEW_PROCESS_GROUP on Windows (start_new_session on POSIX) so the whole
    process tree can be killed later via treekill.kill_process_tree."""
    argv = build_argv(agy_path, prompt_text, cwd, timeout_s, model, effort, skip_permissions, sandbox)
    spawn_kwargs = dict(cwd=cwd, stdout=stdout_fh, stderr=stderr_fh)
    if sys.platform == "win32":
        spawn_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        spawn_kwargs["start_new_session"] = True
    return await asyncio.create_subprocess_exec(*argv, **spawn_kwargs)
