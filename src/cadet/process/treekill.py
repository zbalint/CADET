import os
import platform
import signal
import subprocess
import time

_TASKKILL_PROCESS_NOT_FOUND = 128
_POSIX_KILL_GRACE_S = 5


def kill_process_tree(pid: int) -> None:
    """Best-effort tree-kill. `agy` may spawn its own children (git, npm, node,
    etc.) during a refactor task, so killing only the immediate `agy` process
    (proc.terminate()) is not sufficient.

    Windows (primary target): `taskkill /PID <pid> /T /F`.
    POSIX (documented for portability, not the priority): SIGTERM the process
    group, escalating to SIGKILL after a grace period.
    """
    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, text=True, timeout=10,
            )
        except subprocess.TimeoutExpired:
            return
        # Exit code 128 ("process not found") means it was already dead —
        # not an error for a best-effort kill.
        if result.returncode not in (0, _TASKKILL_PROCESS_NOT_FOUND):
            pass
        return

    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(_POSIX_KILL_GRACE_S)
    try:
        # signal.SIGKILL doesn't exist on Windows; this branch never runs there
        # (guarded by the platform.system() == "Windows" check above), but the
        # attribute lookup must not blow up at import/patch time regardless.
        os.killpg(pgid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except ProcessLookupError:
        pass
