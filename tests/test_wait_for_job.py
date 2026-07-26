import os
import shutil
import tempfile
import unittest
from unittest import mock

from cadet.db import job_store
from cadet.db.schema import init_db
from cadet.process import wait_for_job


class WaitForJobTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = init_db(self.db_path)
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _insert(self, job_id="job-1", status_="pending", **overrides):
        kwargs = dict(
            job_id=job_id, context_id="ctx-1", label="test", prompt_path="p.txt",
            cwd=self.temp_dir, model=None, effort=None, skip_permissions=False,
            status=status_, created_at="2026-07-26T00:00:00", timeout_s=1800,
            stdout_log_path=os.path.join(self.temp_dir, "stdout.log"),
            stderr_log_path=os.path.join(self.temp_dir, "stderr.log"),
            db_path=self.db_path,
        )
        kwargs.update(overrides)
        job_store.insert_job(**kwargs)


class TestWaitForTerminal(WaitForJobTestCase):
    def test_job_not_found_exits_fast_no_sleep(self):
        sleep_fn = mock.Mock()
        result = wait_for_job.wait_for_terminal("does-not-exist", db_path=self.db_path, sleep_fn=sleep_fn)
        self.assertEqual(result["outcome"], "not_found")
        self.assertIsNone(result["job"])
        sleep_fn.assert_not_called()

    def test_already_succeeded_returns_immediately(self):
        self._insert(status_="pending")
        job_store.mark_running("job-1", pid=1, started_at="t1", db_path=self.db_path)
        job_store.finalize_terminal("job-1", status="succeeded", exit_code=0, finished_at="t2", db_path=self.db_path)

        sleep_fn = mock.Mock()
        result = wait_for_job.wait_for_terminal("job-1", db_path=self.db_path, sleep_fn=sleep_fn)
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["job"]["status"], "succeeded")
        sleep_fn.assert_not_called()

    def test_terminal_non_success_status(self):
        self._insert(status_="pending")
        job_store.mark_running("job-1", pid=1, started_at="t1", db_path=self.db_path)
        job_store.finalize_terminal("job-1", status="failed", exit_code=1, finished_at="t2", db_path=self.db_path)

        result = wait_for_job.wait_for_terminal("job-1", db_path=self.db_path, sleep_fn=mock.Mock())
        self.assertEqual(result["outcome"], "terminal_non_success")
        self.assertEqual(result["job"]["status"], "failed")

    def test_max_wait_ceiling_hit_without_terminal_status(self):
        self._insert(status_="pending")
        job_store.mark_running("job-1", pid=1, started_at="t1", db_path=self.db_path)
        # Never finalized — stays "running" for this whole test.

        times = iter([0, 999])  # start=0, first ceiling check already >= max_wait_s
        result = wait_for_job.wait_for_terminal(
            "job-1", db_path=self.db_path, max_wait_s=5,
            sleep_fn=mock.Mock(), time_fn=lambda: next(times),
        )
        self.assertEqual(result["outcome"], "ceiling_hit")
        self.assertEqual(result["job"]["status"], "running")

    def test_polls_more_than_once_before_terminal_state_appears(self):
        """Confirms this is a real poll loop, not a single check: the job stays
        'running' through the first (mocked) sleep and only flips to 'succeeded'
        as a side effect of the second one — so this fails if wait_for_terminal
        only ever calls get_job once."""
        self._insert(status_="pending")
        job_store.mark_running("job-1", pid=1, started_at="t1", db_path=self.db_path)

        call_count = {"n": 0}

        def fake_sleep(_interval):
            call_count["n"] += 1
            if call_count["n"] == 2:
                job_store.finalize_terminal(
                    "job-1", status="succeeded", exit_code=0, finished_at="t2", db_path=self.db_path,
                )

        with mock.patch.object(job_store, "get_job", wraps=job_store.get_job) as spy_get_job:
            result = wait_for_job.wait_for_terminal(
                "job-1", db_path=self.db_path, poll_interval_s=0, max_wait_s=100, sleep_fn=fake_sleep,
            )

        self.assertEqual(result["outcome"], "succeeded")
        self.assertGreaterEqual(call_count["n"], 2)
        # initial read + >=2 loop re-reads
        self.assertGreaterEqual(spy_get_job.call_count, 3)


if __name__ == "__main__":
    unittest.main()
