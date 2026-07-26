import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import ANY, patch

from cadet.db import job_store
from cadet.db.schema import init_db
from cadet.jobs.reconcile import reconcile_on_startup


class FakeDispatcher:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


class ReconcileTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = init_db(self.db_path)
        conn.close()
        self.dispatcher = FakeDispatcher()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _insert(self, job_id, status, pid=None, provider="agy"):
        job_store.insert_job(
            job_id=job_id, context_id="ctx-1", label="test",
            prompt_path=f"/logs/{job_id}/prompt.txt", cwd="C:\\scratch",
            model=None, effort=None, skip_permissions=False, status=status,
            created_at="2026-07-26T00:00:00", timeout_s=1800,
            stdout_log_path=f"/logs/{job_id}/stdout.log",
            stderr_log_path=f"/logs/{job_id}/stderr.log",
            provider=provider,
            db_path=self.db_path,
        )
        if status == "running" and pid is not None:
            job_store.mark_running(job_id, pid=pid, started_at="2026-07-26T00:00:01", db_path=self.db_path)


class TestReconcileOnStartup(ReconcileTestCase):
    async def test_pending_jobs_are_reenqueued(self):
        self._insert("job-pending-1", status="pending")
        self._insert("job-pending-2", status="pending")

        summary = await reconcile_on_startup(self.dispatcher, db_path=self.db_path)

        self.assertEqual(summary["reenqueued"], 2)
        self.assertEqual(set(self.dispatcher.enqueued), {"job-pending-1", "job-pending-2"})
        # Re-enqueuing does not itself change the row's status.
        self.assertEqual(job_store.get_job("job-pending-1", db_path=self.db_path)["status"], "pending")

    async def test_running_job_with_dead_pid_marked_unknown_interrupted(self):
        # A PID this large is vanishingly unlikely to be a live process on any
        # real machine — this exercises the real `tasklist` liveness check
        # rather than mocking it, per the plan's own guidance. Still-native
        # provider: this PID-based path no longer applies to agy/codex (see
        # the container-based tests below).
        self._insert("job-running-dead", status="pending", provider="cursor")
        job_store.mark_running("job-running-dead", pid=999999, started_at="t1", db_path=self.db_path)

        summary = await reconcile_on_startup(self.dispatcher, db_path=self.db_path)

        self.assertEqual(summary["interrupted"], 1)
        job = job_store.get_job("job-running-dead", db_path=self.db_path)
        self.assertEqual(job["status"], "unknown-interrupted")
        self.assertIn("not found at restart", job["error_message"])

    async def test_running_job_with_alive_pid_is_killed_and_marked(self):
        self._insert("job-running-alive", status="pending", provider="cursor")
        job_store.mark_running("job-running-alive", pid=4242, started_at="t1", db_path=self.db_path)

        with patch("cadet.jobs.reconcile._is_pid_alive", return_value=True), \
             patch("cadet.jobs.reconcile.kill_process_tree") as mock_kill:
            await reconcile_on_startup(self.dispatcher, db_path=self.db_path)

        mock_kill.assert_called_once_with(4242)
        job = job_store.get_job("job-running-alive", db_path=self.db_path)
        self.assertEqual(job["status"], "unknown-interrupted")
        self.assertIn("still alive at restart; force-killed", job["error_message"])

    async def test_running_agy_job_stops_container_regardless_of_pid_liveness(self):
        # agy's recorded pid is the docker-run client's, not the container's
        # own lifetime — reconcile must stop the container unconditionally,
        # never PID-liveness-check it. Default provider (None) resolves to
        # "agy" the same way dispatcher.py's "job['provider'] or 'agy'" does.
        self._insert("job-running-agy", status="pending")
        job_store.mark_running("job-running-agy", pid=7777, started_at="t1", db_path=self.db_path)

        with patch("cadet.jobs.reconcile.stop_container") as mock_stop, \
             patch("cadet.jobs.reconcile.kill_process_tree") as mock_kill, \
             patch("cadet.jobs.reconcile._is_pid_alive") as mock_alive:
            await reconcile_on_startup(self.dispatcher, db_path=self.db_path)

        mock_stop.assert_called_once_with("cadet-agy-job-running-agy", ANY)
        mock_kill.assert_not_called()
        mock_alive.assert_not_called()
        job = job_store.get_job("job-running-agy", db_path=self.db_path)
        self.assertEqual(job["status"], "unknown-interrupted")
        self.assertIn("agy container force-stopped", job["error_message"])

    async def test_running_codex_job_stops_container_regardless_of_pid_liveness(self):
        # Mirrors test_running_agy_job_stops_container_regardless_of_pid_liveness
        # — codex is containerized as of Phase 3, same "docker stop
        # unconditionally, never PID-liveness-check" rule as agy.
        self._insert("job-running-codex", status="pending", provider="codex")
        job_store.mark_running("job-running-codex", pid=8888, started_at="t1", db_path=self.db_path)

        with patch("cadet.jobs.reconcile.stop_container") as mock_stop, \
             patch("cadet.jobs.reconcile.kill_process_tree") as mock_kill, \
             patch("cadet.jobs.reconcile._is_pid_alive") as mock_alive:
            await reconcile_on_startup(self.dispatcher, db_path=self.db_path)

        mock_stop.assert_called_once_with("cadet-codex-job-running-codex", ANY)
        mock_kill.assert_not_called()
        mock_alive.assert_not_called()
        job = job_store.get_job("job-running-codex", db_path=self.db_path)
        self.assertEqual(job["status"], "unknown-interrupted")
        self.assertIn("codex container force-stopped", job["error_message"])

    async def test_terminal_and_running_pending_rows_untouched(self):
        self._insert("job-succeeded", status="pending")
        job_store.mark_running("job-succeeded", pid=1, started_at="t1", db_path=self.db_path)
        job_store.finalize_terminal("job-succeeded", status="succeeded", exit_code=0, finished_at="t2", db_path=self.db_path)

        await reconcile_on_startup(self.dispatcher, db_path=self.db_path)

        self.assertEqual(job_store.get_job("job-succeeded", db_path=self.db_path)["status"], "succeeded")
        self.assertNotIn("job-succeeded", self.dispatcher.enqueued)


if __name__ == "__main__":
    unittest.main()
