import asyncio
import datetime as dt
import os
import shutil
import tempfile
import unittest

from cadet import config
from cadet.db import job_store
from cadet.db.schema import init_db
from cadet.jobs.retention import retention_sweep_loop, sweep_once

_ENV_VARS = ["CADET_STATE_DIR", "CADET_LOG_RETENTION_DAYS"]


class RetentionTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved_env = {var: os.environ.get(var) for var in _ENV_VARS}
        self.temp_dir = tempfile.mkdtemp()
        os.environ["CADET_STATE_DIR"] = self.temp_dir
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = init_db(self.db_path)
        conn.close()

    def tearDown(self):
        for var, val in self._saved_env.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _insert_terminal_job(self, job_id, status, finished_at, make_log_dir=True):
        job_store.insert_job(
            job_id=job_id, context_id="ctx-1", label="test",
            prompt_path=f"/logs/{job_id}/prompt.txt", cwd="C:\\scratch",
            model=None, effort=None, skip_permissions=False, status="pending",
            created_at="2020-01-01T00:00:00", timeout_s=1800,
            stdout_log_path=f"/logs/{job_id}/stdout.log",
            stderr_log_path=f"/logs/{job_id}/stderr.log",
            db_path=self.db_path,
        )
        job_store.mark_running(job_id, pid=1, started_at="2020-01-01T00:00:01", db_path=self.db_path)
        job_store.finalize_terminal(job_id, status=status, exit_code=0, finished_at=finished_at, db_path=self.db_path)
        if make_log_dir:
            job_dir = os.path.join(config.get_logs_dir(), job_id)
            os.makedirs(job_dir, exist_ok=True)
            with open(os.path.join(job_dir, "stdout.log"), "w") as f:
                f.write("hello")
        return job_id

    def _insert_pending_job(self, job_id):
        job_store.insert_job(
            job_id=job_id, context_id="ctx-1", label="test",
            prompt_path=f"/logs/{job_id}/prompt.txt", cwd="C:\\scratch",
            model=None, effort=None, skip_permissions=False, status="pending",
            created_at="2020-01-01T00:00:00", timeout_s=1800,
            stdout_log_path=f"/logs/{job_id}/stdout.log",
            stderr_log_path=f"/logs/{job_id}/stderr.log",
            db_path=self.db_path,
        )


class TestSweepOnce(RetentionTestCase):
    def test_deletes_old_terminal_job_row_and_log_dir(self):
        job_id = self._insert_terminal_job("job-old", "succeeded", finished_at="2020-01-01T00:00:00")
        job_log_dir = os.path.join(config.get_logs_dir(), job_id)
        self.assertTrue(os.path.isdir(job_log_dir))

        deleted = sweep_once(retention_days=14, db_path=self.db_path)

        self.assertEqual(deleted, ["job-old"])
        self.assertIsNone(job_store.get_job("job-old", db_path=self.db_path))
        self.assertFalse(os.path.exists(job_log_dir))

    def test_recent_terminal_job_survives(self):
        recent_finished_at = dt.datetime.now().isoformat(timespec="seconds")
        self._insert_terminal_job("job-recent", "succeeded", finished_at=recent_finished_at)

        deleted = sweep_once(retention_days=14, db_path=self.db_path)

        self.assertEqual(deleted, [])
        self.assertIsNotNone(job_store.get_job("job-recent", db_path=self.db_path))

    def test_pending_and_running_rows_never_swept_regardless_of_age(self):
        self._insert_pending_job("job-pending")
        self.assertIsNotNone(job_store.get_job("job-pending", db_path=self.db_path))

        deleted = sweep_once(retention_days=0, db_path=self.db_path)

        self.assertNotIn("job-pending", deleted)
        self.assertIsNotNone(job_store.get_job("job-pending", db_path=self.db_path))

    def test_missing_log_dir_does_not_raise(self):
        self._insert_terminal_job("job-old-no-dir", "failed", finished_at="2020-01-01T00:00:00", make_log_dir=False)
        deleted = sweep_once(retention_days=14, db_path=self.db_path)
        self.assertEqual(deleted, ["job-old-no-dir"])


class TestRetentionSweepLoop(RetentionTestCase):
    async def test_sweeps_immediately_before_first_sleep(self):
        job_id = self._insert_terminal_job("job-old", "succeeded", finished_at="2020-01-01T00:00:00")

        task = asyncio.create_task(retention_sweep_loop(interval_s=3600, retention_days=14, db_path=self.db_path))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        self.assertIsNone(job_store.get_job(job_id, db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
