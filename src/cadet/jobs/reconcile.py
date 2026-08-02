import asyncio
import datetime as dt
import os
import platform
import subprocess

from cadet import config
from cadet.db import job_store
from cadet.process.launcher import container_name_for_job
from cadet.process.treekill import kill_process_tree, stop_container


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _is_pid_alive(pid: int) -> bool:
    """Best-effort liveness check (PID-reuse false positives are an accepted,
    low-probability edge case — see JOB_LIFECYCLE.md's "Startup reconciliation").
    Windows: parse `tasklist /FI "PID eq <pid>" /NH` for a matching line.
    POSIX: os.kill(pid, 0)."""
    if platform.system() == "Windows":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        )
        return str(pid) in result.stdout

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just owned by someone else
    else:
        return True


async def reconcile_on_startup(dispatcher, db_path=None) -> dict:
    """Run once, inside the FastMCP lifespan hook, before the dispatcher's own
    loop starts consuming from the queue.

    - `pending` rows: no OS process ever existed for these — simply re-enqueue.
    - `running` rows: check `owner_pid`. If set and `_is_pid_alive(owner_pid)` is
      True, skip it entirely (it belongs to a live sibling server instance).
      Otherwise, if `owner_pid` is NULL (legacy row from before WP1 migration) or
      the owner PID is dead, run the kill-and-mark-interrupted logic: for
      containerized providers (`agy`, `codex`, `cursor`, `copilot`), stop the
      container; for native PIDs, tree-kill if alive; then mark the row
      `unknown-interrupted`.

    Returns a small summary dict for startup logging.
    """
    pending_jobs = job_store.list_jobs(status_filter="pending", limit=10_000, db_path=db_path)
    for job in pending_jobs:
        await dispatcher.enqueue(job["job_id"])

    running_jobs = job_store.list_jobs(status_filter="running", limit=10_000, db_path=db_path)
    interrupted_count = 0
    for job in running_jobs:
        owner_pid = job.get("owner_pid")
        if owner_pid is not None and _is_pid_alive(owner_pid):
            continue

        provider_name = job["provider"] or "agy"
        pid = job["pid"]
        if provider_name in ("agy", "codex", "cursor", "copilot"):
            # The recorded pid is the docker-run client's PID from the
            # previous CADET instance, not the container's own lifetime -- a
            # dead client PID does NOT mean the container (a daemon-managed
            # object independent of that client) is dead. No liveness
            # pre-check needed: docker stop on an already-gone container is
            # itself a tolerated no-op, unlike the PID check-then-kill below.
            grace_s = {
                "agy": config.get_agy_stop_grace_s,
                "codex": config.get_codex_stop_grace_s,
                "cursor": config.get_cursor_stop_grace_s,
                "copilot": config.get_copilot_stop_grace_s,
            }[provider_name]()
            await asyncio.to_thread(
                stop_container, container_name_for_job(provider_name, job["job_id"]), grace_s
            )
            error_message = f"{provider_name} container force-stopped at restart (previous CADET instance's lifetime unknown)"
        elif pid is None:
            error_message = "no pid recorded at restart"
        elif _is_pid_alive(pid):
            await asyncio.to_thread(kill_process_tree, pid)
            error_message = f"pid {pid} still alive at restart; force-killed"
        else:
            error_message = f"pid {pid} not found at restart"
        job_store.mark_unknown_interrupted(
            job["job_id"], error_message=error_message, finished_at=_now_iso(), db_path=db_path
        )
        interrupted_count += 1

    return {"reenqueued": len(pending_jobs), "interrupted": interrupted_count}
