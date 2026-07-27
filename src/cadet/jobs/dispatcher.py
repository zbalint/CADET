import asyncio
import datetime as dt

from cadet import config
from cadet.db import job_store
from cadet.process.providers.agy import spawn as spawn_agy, parse_error as parse_error_agy
from cadet.process.providers.codex import spawn as spawn_codex, parse_error as parse_error_codex, stop as stop_codex_container
from cadet.process.providers.cursor import spawn as spawn_cursor, parse_error as parse_error_cursor, stop as stop_cursor_container
from cadet.process.providers.copilot import spawn as spawn_copilot, parse_error as parse_error_copilot
from cadet.process.launcher import stop_agy as stop_agy_container
from cadet.process.treekill import kill_process_tree

# Providers whose spawn() needs job_id to derive a deterministic container
# name (see launcher.container_name_for_job) -- all three are containerized
# (Phase 2: agy, Phase 3: codex, Phase 4: cursor); copilot's spawn()
# signature is untouched and never receives it.
_CONTAINERIZED_PROVIDERS = ("agy", "codex", "cursor")

# NOTE: dispatch dicts referencing spawn_agy/parse_error_agy are built fresh
# inside run_job (not module scope) so that unittest.mock.patch(
# "cadet.jobs.dispatcher.spawn_agy", ...) — which rebinds this module's own
# global name — is still picked up. A module-scope dict built once at import
# time would freeze the original reference and silently ignore the patch.


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _read_tail(path, max_chars=4000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()[-max_chars:]
    except OSError:
        return ""


class Dispatcher:
    """Owns the pending-job queue and the CADET_MAX_CONCURRENT-sized semaphore.
    `delegate_task` only ever inserts a `pending` row and calls `enqueue` — it
    never awaits subprocess completion itself. One long-lived loop (`start`)
    owns dispatch; each dispatched job runs as its own independent task."""

    def __init__(self, executable_paths: dict, max_concurrent: int | None = None, db_path: str | None = None):
        self.executable_paths = executable_paths
        self.db_path = db_path
        self._queue: asyncio.Queue = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent or config.get_max_concurrent())
        self._cancel_flags: set = set()
        self._dispatcher_task = None

    def start(self) -> None:
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())

    async def _dispatch_loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            await self._semaphore.acquire()
            asyncio.create_task(self.run_job(job_id))

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    @staticmethod
    def _stop_fns() -> dict:
        """Provider-aware stop dispatch, shared by run_job's timeout/lost-race
        paths and cancel()'s running-job path. Built fresh on every call (not
        module scope) so unittest.mock.patch on the module-level
        stop_agy_container/stop_codex_container/kill_process_tree names is
        still honored -- a dict built once at import time would freeze the
        original reference and silently ignore the patch, same reasoning as
        spawn_fns/parse_error_fns below."""
        return {
            "agy": lambda pid, jid: stop_agy_container(jid, pid),
            "codex": lambda pid, jid: stop_codex_container(jid, pid),
            "cursor": lambda pid, jid: stop_cursor_container(jid, pid),
            "copilot": lambda pid, jid: kill_process_tree(pid),
        }

    async def run_job(self, job_id: str) -> None:
        stdout_fh = None
        stderr_fh = None
        try:
            job = job_store.get_job(job_id, db_path=self.db_path)
            if job is None or job["status"] != "pending":
                # Re-check immediately before spawning: catches a cancel_task
                # that raced in while this job was still queued.
                return

            with open(job["prompt_path"], "r", encoding="utf-8") as f:
                prompt_text = f.read()

            stdout_fh = open(job["stdout_log_path"], "ab")
            stderr_fh = open(job["stderr_log_path"], "ab")

            provider_name = job["provider"] or "agy"
            spawn_fns = {"agy": spawn_agy, "codex": spawn_codex, "cursor": spawn_cursor, "copilot": spawn_copilot}
            spawn_fn = spawn_fns[provider_name]
            extra_kwargs = {"job_id": job_id} if provider_name in _CONTAINERIZED_PROVIDERS else {}

            proc = await spawn_fn(
                self.executable_paths[provider_name], prompt_text, job["cwd"], job["timeout_s"],
                stdout_fh, stderr_fh,
                model=job["model"], effort=job["effort"],
                skip_permissions=bool(job["skip_permissions"]),
                sandbox=config.is_provider_sandbox_enabled(provider_name),
                **extra_kwargs,
            )

            # Provider-aware stop: for agy/codex, the recorded pid is the
            # docker-run client's PID, not the container's own lifetime, so
            # tree-killing it alone does not reliably stop the container (see
            # launcher.stop_agy / providers.codex.stop / treekill.stop_container).
            stop_fn = self._stop_fns()[provider_name]

            won = job_store.mark_running(job_id, proc.pid, _now_iso(), db_path=self.db_path)
            if not won:
                # Lost the race to a cancel_task that landed between the pending
                # check above and this write. The row is already terminal
                # ('cancelled'); the process is real, so kill it and stop —
                # no terminal write of our own is needed or would succeed.
                await asyncio.to_thread(stop_fn, proc.pid, job_id)
                return

            timed_out = False
            try:
                exit_code = await asyncio.wait_for(proc.wait(), timeout=job["timeout_s"])
            except asyncio.TimeoutError:
                await asyncio.to_thread(stop_fn, proc.pid, job_id)
                exit_code = await proc.wait()
                timed_out = True

            finished_at = _now_iso()
            error_message = None
            error_kind = None
            quota_reset_at = None

            if job_id in self._cancel_flags:
                status = "cancelled"
            elif timed_out:
                status = "timeout"
            elif exit_code == 0:
                status = "succeeded"
            else:
                status = "failed"
                stderr_fh.flush()
                parse_error_fns = {
                    "agy": parse_error_agy, "codex": parse_error_codex,
                    "cursor": parse_error_cursor, "copilot": parse_error_copilot,
                }
                error_kind, quota_reset_at = parse_error_fns[provider_name](
                    _read_tail(job["stderr_log_path"]), finished_at
                )
                error_message = f"{provider_name} exited {exit_code}"

            job_store.finalize_terminal(
                job_id, status=status, exit_code=exit_code, finished_at=finished_at,
                error_message=error_message, error_kind=error_kind,
                quota_reset_at=quota_reset_at, db_path=self.db_path,
            )
        except Exception as exc:  # defensive: run_job must always finalize or no-op cleanly
            # May fire before or after mark_running, so use the more permissive
            # (pending-or-running) conditional write rather than finalize_terminal.
            job_store.force_fail(
                job_id, error_message=f"CADET internal error: {exc}",
                finished_at=_now_iso(), db_path=self.db_path,
            )
        finally:
            self._cancel_flags.discard(job_id)
            if stdout_fh:
                stdout_fh.close()
            if stderr_fh:
                stderr_fh.close()
            self._semaphore.release()

    async def cancel(self, job_id: str):
        job = job_store.get_job(job_id, db_path=self.db_path)
        if job is None:
            return None

        status = job["status"]
        if status in job_store.TERMINAL_STATUSES:
            return {"previous_status": status, "status": status, "already_terminal": True}

        if status == "pending":
            won = job_store.mark_cancelled_pending(job_id, finished_at=_now_iso(), db_path=self.db_path)
            if won:
                return {"previous_status": "pending", "status": "cancelled", "already_terminal": False}
            # Lost the race — dispatcher has since started it. Re-fetch and fall
            # through to the running-job path below with fresh state.
            job = job_store.get_job(job_id, db_path=self.db_path)
            status = job["status"] if job else status

        if status == "running":
            # No DB-level conditional protects a running->cancelled write the
            # way pending->running/terminal writes do, so a single in-memory
            # flag decides the eventual status; run_job's own completion path
            # (the sole writer for a running job) checks it before finalizing.
            self._cancel_flags.add(job_id)
            if job.get("pid"):
                provider_name = job["provider"] or "agy"
                stop_fn = self._stop_fns()[provider_name]
                await asyncio.to_thread(stop_fn, job["pid"], job_id)
            return {"previous_status": "running", "status": "cancelled", "already_terminal": False}

        # Finished between our checks.
        return {"previous_status": status, "status": status, "already_terminal": True}
