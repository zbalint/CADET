import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cadet.db import job_store, provider_status_store
from cadet.db.schema import init_db
from cadet.jobs.dispatcher import Dispatcher


def _make_proc(pid=1234, wait_return=0):
    proc = MagicMock()
    proc.pid = pid
    proc.wait = AsyncMock(return_value=wait_return)
    return proc


class DispatcherTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = init_db(self.db_path)
        conn.close()
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "C:\\tools\\agy.exe"}, max_concurrent=2, db_path=self.db_path
        )

    def tearDown(self):
        if self.dispatcher._dispatcher_task is not None:
            self.dispatcher._dispatcher_task.cancel()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_pending_job(self, job_id="job-1", cwd=None, timeout_s=30, prompt_text="do the thing", **overrides):
        cwd = cwd or self.temp_dir
        job_dir = os.path.join(self.temp_dir, "logs", job_id)
        os.makedirs(job_dir, exist_ok=True)
        prompt_path = os.path.join(job_dir, "prompt.txt")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt_text)
        stdout_log_path = os.path.join(job_dir, "stdout.log")
        stderr_log_path = os.path.join(job_dir, "stderr.log")

        kwargs = dict(
            job_id=job_id, context_id="ctx-1", label="test", prompt_path=prompt_path,
            cwd=cwd, model=None, effort=None, skip_permissions=False, status="pending",
            created_at="2026-07-26T00:00:00", timeout_s=timeout_s,
            stdout_log_path=stdout_log_path, stderr_log_path=stderr_log_path,
            db_path=self.db_path,
        )
        kwargs.update(overrides)
        job_store.insert_job(**kwargs)
        return stdout_log_path, stderr_log_path


class TestRunJobLifecycle(DispatcherTestCase):
    async def test_succeeds_on_exit_code_zero(self):
        self._create_pending_job()
        with patch("cadet.jobs.dispatcher.spawn_agy", AsyncMock(return_value=_make_proc(pid=111, wait_return=0))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["exit_code"], 0)
        self.assertEqual(job["pid"], 111)
        self.assertEqual(self.dispatcher._semaphore._value, 2)

    async def test_fails_on_nonzero_exit_code(self):
        self._create_pending_job()
        with patch("cadet.jobs.dispatcher.spawn_agy", AsyncMock(return_value=_make_proc(wait_return=1))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["exit_code"], 1)
        self.assertIsNone(job["error_kind"])

    async def test_failed_job_detects_quota_exhaustion_from_stderr(self):
        _, stderr_log_path = self._create_pending_job()
        with open(stderr_log_path, "w", encoding="utf-8") as f:
            f.write("Error: Individual quota reached. Please upgrade. Resets in 5m.\n")

        with patch("cadet.jobs.dispatcher.spawn_agy", AsyncMock(return_value=_make_proc(wait_return=1))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_kind"], "quota_exhausted")
        self.assertIsNotNone(job["quota_reset_at"])

    async def test_not_pending_job_is_a_noop(self):
        self._create_pending_job(status="cancelled")
        mock_spawn = AsyncMock()
        with patch("cadet.jobs.dispatcher.spawn_agy", mock_spawn):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        mock_spawn.assert_not_called()
        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(self.dispatcher._semaphore._value, 2)

    async def test_timeout_kills_process_and_marks_timeout(self):
        self._create_pending_job(timeout_s=0.05)
        wait_calls = {"n": 0}

        async def fake_wait():
            wait_calls["n"] += 1
            if wait_calls["n"] == 1:
                await asyncio.sleep(10)
            return -9

        proc = MagicMock()
        proc.pid = 222
        proc.wait = fake_wait

        with patch("cadet.jobs.dispatcher.spawn_agy", AsyncMock(return_value=proc)), \
             patch("cadet.jobs.dispatcher.stop_agy_container") as mock_stop:
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        # agy's timeout path stops the container (docker stop), not a bare
        # PID tree-kill — the recorded pid is the docker-run client's, not
        # the container's own lifetime. See launcher.stop_agy.
        mock_stop.assert_called_once_with("job-1", 222)
        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "timeout")

    async def test_timeout_for_codex_provider_stops_container(self):
        # codex is containerized as of Phase 3 (mirrors agy's Phase 2): the
        # recorded pid is the docker-run client's, not the container's own
        # lifetime. See providers.codex.stop.
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "cadet-agy:latest", "codex": "cadet-codex:latest"},
            max_concurrent=2, db_path=self.db_path,
        )
        self._create_pending_job(provider="codex", timeout_s=0.05)
        wait_calls = {"n": 0}

        async def fake_wait():
            wait_calls["n"] += 1
            if wait_calls["n"] == 1:
                await asyncio.sleep(10)
            return -9

        proc = MagicMock()
        proc.pid = 223
        proc.wait = fake_wait

        with patch("cadet.jobs.dispatcher.spawn_codex", AsyncMock(return_value=proc)), \
             patch("cadet.jobs.dispatcher.kill_process_tree") as mock_kill, \
             patch("cadet.jobs.dispatcher.stop_codex_container") as mock_stop:
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        mock_stop.assert_called_once_with("job-1", 223)
        mock_kill.assert_not_called()
        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "timeout")

    async def test_timeout_for_cursor_provider_stops_container(self):
        # cursor is containerized as of Phase 4 (mirrors codex's Phase 3): the
        # recorded pid is the docker-run client's, not the container's own
        # lifetime. See providers.cursor.stop.
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "cadet-agy:latest", "cursor": "cadet-cursor:latest"},
            max_concurrent=2, db_path=self.db_path,
        )
        self._create_pending_job(provider="cursor", timeout_s=0.05)
        wait_calls = {"n": 0}

        async def fake_wait():
            wait_calls["n"] += 1
            if wait_calls["n"] == 1:
                await asyncio.sleep(10)
            return -9

        proc = MagicMock()
        proc.pid = 224
        proc.wait = fake_wait

        with patch("cadet.jobs.dispatcher.spawn_cursor", AsyncMock(return_value=proc)), \
             patch("cadet.jobs.dispatcher.kill_process_tree") as mock_kill, \
             patch("cadet.jobs.dispatcher.stop_cursor_container") as mock_stop:
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        mock_stop.assert_called_once_with("job-1", 224)
        mock_kill.assert_not_called()
        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "timeout")

    async def test_lost_mark_running_race_kills_orphan_and_does_not_finalize(self):
        self._create_pending_job()
        proc = _make_proc(pid=333)
        with patch("cadet.jobs.dispatcher.spawn_agy", AsyncMock(return_value=proc)), \
             patch("cadet.jobs.dispatcher.job_store.mark_running", return_value=False), \
             patch("cadet.jobs.dispatcher.stop_agy_container") as mock_stop:
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        mock_stop.assert_called_once_with("job-1", 333)
        job = job_store.get_job("job-1", db_path=self.db_path)
        # Row was never actually flipped to running (mocked to fail), stays pending —
        # proves run_job didn't blindly finalize over a row it lost the race on.
        self.assertEqual(job["status"], "pending")
        self.assertEqual(self.dispatcher._semaphore._value, 2)

    async def test_semaphore_released_even_on_unexpected_exception(self):
        self._create_pending_job()
        with patch("cadet.jobs.dispatcher.spawn_agy", AsyncMock(side_effect=RuntimeError("boom"))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        self.assertEqual(self.dispatcher._semaphore._value, 2)
        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "failed")
        self.assertIn("CADET internal error", job["error_message"])

    async def test_unconfigured_provider_fails_gracefully(self):
        self._create_pending_job(provider="codex")
        await self.dispatcher._semaphore.acquire()
        await self.dispatcher.run_job("job-1")

        self.assertEqual(self.dispatcher._semaphore._value, 2)
        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "failed")
        self.assertIn("CADET internal error", job["error_message"])

    async def test_configured_codex_provider_dispatches_via_spawn_codex(self):
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "C:\\tools\\agy.exe", "codex": "C:\\tools\\codex.exe"},
            max_concurrent=2, db_path=self.db_path,
        )
        self._create_pending_job(provider="codex")
        with patch("cadet.jobs.dispatcher.spawn_codex", AsyncMock(return_value=_make_proc(pid=444, wait_return=0))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["pid"], 444)

    async def test_configured_cursor_provider_dispatches_via_spawn_cursor(self):
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "C:\\tools\\agy.exe", "cursor": "cadet-cursor:latest"},
            max_concurrent=2, db_path=self.db_path,
        )
        self._create_pending_job(provider="cursor")
        with patch("cadet.jobs.dispatcher.spawn_cursor", AsyncMock(return_value=_make_proc(pid=555, wait_return=0))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["pid"], 555)

    async def test_configured_copilot_provider_dispatches_via_spawn_copilot(self):
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "C:\\tools\\agy.exe", "copilot": "C:\\tools\\copilot.cmd"},
            max_concurrent=2, db_path=self.db_path,
        )
        self._create_pending_job(provider="copilot")
        with patch("cadet.jobs.dispatcher.spawn_copilot", AsyncMock(return_value=_make_proc(pid=666, wait_return=0))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["pid"], 666)


class TestQuotaGate(DispatcherTestCase):
    async def test_blocked_job_never_calls_spawn(self):
        self._create_pending_job()
        provider_status_store.upsert_exhaustion(
            "agy:model:none", "2099-01-01T00:00:00", "confirmed", "job-0", "2026-07-27T00:00:00",
            db_path=self.db_path,
        )
        mock_spawn = AsyncMock()
        with patch("cadet.jobs.dispatcher.spawn_agy", mock_spawn):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        mock_spawn.assert_not_called()
        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_kind"], "quota_exhausted")
        self.assertEqual(job["quota_reset_at"], "2099-01-01T00:00:00")
        self.assertEqual(job["quota_reset_confidence"], "confirmed")
        self.assertEqual(self.dispatcher._semaphore._value, 2)

    async def test_skip_quota_check_bypasses_a_blocking_row(self):
        self._create_pending_job(skip_quota_check=True)
        provider_status_store.upsert_exhaustion(
            "agy:model:none", "2099-01-01T00:00:00", "confirmed", "job-0", "2026-07-27T00:00:00",
            db_path=self.db_path,
        )
        with patch("cadet.jobs.dispatcher.spawn_agy", AsyncMock(return_value=_make_proc(pid=777, wait_return=0))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["pid"], 777)

    async def test_already_past_reset_time_does_not_block(self):
        self._create_pending_job()
        provider_status_store.upsert_exhaustion(
            "agy:model:none", "2020-01-01T00:00:00", "confirmed", "job-0", "2019-12-31T00:00:00",
            db_path=self.db_path,
        )
        with patch("cadet.jobs.dispatcher.spawn_agy", AsyncMock(return_value=_make_proc(pid=888, wait_return=0))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["pid"], 888)
        # Gate self-heals: the stale row should have been cleared.
        self.assertIsNone(provider_status_store.get_status("agy:model:none", db_path=self.db_path))

    async def test_two_queued_jobs_for_same_exhausted_pool_both_blocked(self):
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "C:\\tools\\agy.exe"}, max_concurrent=2, db_path=self.db_path
        )
        self._create_pending_job(job_id="job-a")
        self._create_pending_job(job_id="job-b")
        provider_status_store.upsert_exhaustion(
            "agy:model:none", "2099-01-01T00:00:00", "confirmed", "job-0", "2026-07-27T00:00:00",
            db_path=self.db_path,
        )
        mock_spawn = AsyncMock()
        with patch("cadet.jobs.dispatcher.spawn_agy", mock_spawn):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-a")
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-b")

        mock_spawn.assert_not_called()
        self.assertEqual(job_store.get_job("job-a", db_path=self.db_path)["status"], "failed")
        self.assertEqual(job_store.get_job("job-b", db_path=self.db_path)["status"], "failed")

    async def test_real_quota_failure_populates_provider_status_confirmed(self):
        _, stderr_log_path = self._create_pending_job()
        with open(stderr_log_path, "w", encoding="utf-8") as f:
            f.write("Error: Individual quota reached. Please upgrade. Resets in 5m.\n")

        with patch("cadet.jobs.dispatcher.spawn_agy", AsyncMock(return_value=_make_proc(wait_return=1))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        row = provider_status_store.get_status("agy:model:none", db_path=self.db_path)
        self.assertIsNotNone(row)
        self.assertEqual(row["confidence"], "confirmed")
        self.assertIsNotNone(row["quota_reset_at"])

    async def test_real_quota_failure_with_no_vendor_eta_populates_estimated(self):
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "C:\\tools\\agy.exe", "cursor": "cadet-cursor:latest"},
            max_concurrent=2, db_path=self.db_path,
        )
        _, stderr_log_path = self._create_pending_job(provider="cursor")
        with open(stderr_log_path, "w", encoding="utf-8") as f:
            f.write("ActionRequiredError: You've hit your usage limit Get Cursor Pro for more Agent usage.\n")

        with patch("cadet.jobs.dispatcher.spawn_cursor", AsyncMock(return_value=_make_proc(wait_return=1))):
            await self.dispatcher._semaphore.acquire()
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["error_kind"], "quota_exhausted")
        self.assertEqual(job["quota_reset_confidence"], "estimated")
        self.assertIsNotNone(job["quota_reset_at"])

        row = provider_status_store.get_status("cursor", db_path=self.db_path)
        self.assertIsNotNone(row)
        self.assertEqual(row["confidence"], "estimated")


class TestCancel(DispatcherTestCase):
    async def test_cancel_pending_job(self):
        self._create_pending_job()
        result = await self.dispatcher.cancel("job-1")
        self.assertEqual(result, {"previous_status": "pending", "status": "cancelled", "already_terminal": False})
        self.assertEqual(job_store.get_job("job-1", db_path=self.db_path)["status"], "cancelled")

    async def test_cancel_missing_job_returns_none(self):
        self.assertIsNone(await self.dispatcher.cancel("no-such-job"))

    async def test_cancel_already_terminal_is_idempotent(self):
        self._create_pending_job()
        job_store.mark_running("job-1", pid=1, started_at="t1", db_path=self.db_path)
        job_store.finalize_terminal("job-1", status="succeeded", exit_code=0, finished_at="t2", db_path=self.db_path)

        result = await self.dispatcher.cancel("job-1")
        self.assertEqual(result, {"previous_status": "succeeded", "status": "succeeded", "already_terminal": True})

    async def test_cancel_running_job_sets_flag_and_kills(self):
        self._create_pending_job()
        job_store.mark_running("job-1", pid=555, started_at="t1", db_path=self.db_path)

        with patch("cadet.jobs.dispatcher.stop_agy_container") as mock_stop:
            result = await self.dispatcher.cancel("job-1")

        self.assertEqual(result, {"previous_status": "running", "status": "cancelled", "already_terminal": False})
        self.assertIn("job-1", self.dispatcher._cancel_flags)
        mock_stop.assert_called_once_with("job-1", 555)
        # The DB row itself is left 'running' — run_job's own completion path
        # (the sole writer for a running job) is what finalizes it, per the
        # single-writer design documented in dispatcher.cancel's docstring.
        self.assertEqual(job_store.get_job("job-1", db_path=self.db_path)["status"], "running")

    async def test_cancel_running_job_for_codex_provider_stops_container(self):
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "cadet-agy:latest", "codex": "cadet-codex:latest"},
            max_concurrent=2, db_path=self.db_path,
        )
        self._create_pending_job(provider="codex")
        job_store.mark_running("job-1", pid=557, started_at="t1", db_path=self.db_path)

        with patch("cadet.jobs.dispatcher.kill_process_tree") as mock_kill, \
             patch("cadet.jobs.dispatcher.stop_codex_container") as mock_stop:
            await self.dispatcher.cancel("job-1")

        mock_stop.assert_called_once_with("job-1", 557)
        mock_kill.assert_not_called()

    async def test_cancel_running_job_for_cursor_provider_stops_container(self):
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "cadet-agy:latest", "cursor": "cadet-cursor:latest"},
            max_concurrent=2, db_path=self.db_path,
        )
        self._create_pending_job(provider="cursor")
        job_store.mark_running("job-1", pid=558, started_at="t1", db_path=self.db_path)

        with patch("cadet.jobs.dispatcher.kill_process_tree") as mock_kill, \
             patch("cadet.jobs.dispatcher.stop_cursor_container") as mock_stop:
            await self.dispatcher.cancel("job-1")

        mock_stop.assert_called_once_with("job-1", 558)
        mock_kill.assert_not_called()

    async def test_cancel_flag_causes_run_job_to_finalize_as_cancelled(self):
        self._create_pending_job(timeout_s=5)
        proc = _make_proc(pid=666, wait_return=0)

        async def fake_spawn(*args, **kwargs):
            return proc

        with patch("cadet.jobs.dispatcher.spawn_agy", fake_spawn), \
             patch("cadet.jobs.dispatcher.kill_process_tree"):
            await self.dispatcher._semaphore.acquire()
            # Simulate a cancel_task landing while this job is running: the flag
            # is set externally (as dispatcher.cancel would do); run_job itself
            # still owns the pending->running transition and the eventual write.
            self.dispatcher._cancel_flags.add("job-1")
            await self.dispatcher.run_job("job-1")

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "cancelled")
        self.assertNotIn("job-1", self.dispatcher._cancel_flags)


class TestDispatchLoopConcurrency(DispatcherTestCase):
    async def test_respects_max_concurrent_and_processes_queue_in_order(self):
        self.dispatcher = Dispatcher(
            executable_paths={"agy": "C:\\tools\\agy.exe"}, max_concurrent=1, db_path=self.db_path
        )
        cwd_a = os.path.join(self.temp_dir, "proj-a")
        cwd_b = os.path.join(self.temp_dir, "proj-b")
        os.makedirs(cwd_a, exist_ok=True)
        os.makedirs(cwd_b, exist_ok=True)
        self._create_pending_job(job_id="job-a", cwd=cwd_a, timeout_s=5)
        self._create_pending_job(job_id="job-b", cwd=cwd_b, timeout_s=5)

        started_order = []
        events = {cwd_a: asyncio.Event(), cwd_b: asyncio.Event()}

        async def fake_spawn_agy(agy_path, prompt_text, cwd, timeout_s, stdout_fh, stderr_fh, **kwargs):
            started_order.append(cwd)
            proc = MagicMock()
            proc.pid = 1000 + len(started_order)
            event = events[cwd]

            async def wait():
                await event.wait()
                return 0

            proc.wait = wait
            return proc

        with patch("cadet.jobs.dispatcher.spawn_agy", fake_spawn_agy):
            self.dispatcher.start()
            await self.dispatcher.enqueue("job-a")
            await self.dispatcher.enqueue("job-b")
            await asyncio.sleep(0.1)

            self.assertEqual(started_order, [cwd_a])
            self.assertEqual(job_store.get_job("job-a", db_path=self.db_path)["status"], "running")
            self.assertEqual(job_store.get_job("job-b", db_path=self.db_path)["status"], "pending")

            events[cwd_a].set()
            await asyncio.sleep(0.1)

            self.assertEqual(started_order, [cwd_a, cwd_b])
            self.assertEqual(job_store.get_job("job-a", db_path=self.db_path)["status"], "succeeded")
            self.assertEqual(job_store.get_job("job-b", db_path=self.db_path)["status"], "running")

            events[cwd_b].set()
            await asyncio.sleep(0.1)

            self.assertEqual(job_store.get_job("job-b", db_path=self.db_path)["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
