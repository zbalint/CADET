import os
import shutil
import tempfile
import unittest

from cadet.db import job_store
from cadet.db.schema import init_db


class JobStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.conn = init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _insert(self, job_id="job-1", context_id="ctx-1", status="pending", **overrides):
        kwargs = dict(
            job_id=job_id,
            context_id=context_id,
            label="test-label",
            prompt_path=f"/logs/{job_id}/prompt.txt",
            cwd="C:\\scratch",
            model=None,
            effort=None,
            skip_permissions=False,
            status=status,
            created_at="2026-07-26T00:00:00",
            timeout_s=1800,
            stdout_log_path=f"/logs/{job_id}/stdout.log",
            stderr_log_path=f"/logs/{job_id}/stderr.log",
            db_connection=self.conn,
        )
        kwargs.update(overrides)
        job_store.insert_job(**kwargs)


class TestInsertAndGet(JobStoreTestCase):
    def test_insert_then_get_roundtrip(self):
        self._insert()
        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertIsNotNone(job)
        self.assertEqual(job["job_id"], "job-1")
        self.assertEqual(job["context_id"], "ctx-1")
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["skip_permissions"], 0)

    def test_get_missing_job_returns_none(self):
        self.assertIsNone(job_store.get_job("does-not-exist", db_connection=self.conn))

    def test_provider_defaults_to_agy_when_omitted(self):
        self._insert()
        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertEqual(job["provider"], "agy")

    def test_provider_round_trips_when_explicit(self):
        self._insert(provider="codex")
        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertEqual(job["provider"], "codex")


class TestMarkRunning(JobStoreTestCase):
    def test_mark_running_from_pending_succeeds(self):
        self._insert(status="pending")
        won = job_store.mark_running(
            "job-1", pid=1234, started_at="2026-07-26T00:00:01",
            owner_pid=5678, server_instance_id="srv-abc",
            db_connection=self.conn,
        )
        self.assertTrue(won)
        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["pid"], 1234)
        self.assertEqual(job["owner_pid"], 5678)
        self.assertEqual(job["server_instance_id"], "srv-abc")

    def test_mark_running_from_non_pending_noops(self):
        self._insert(status="cancelled")
        won = job_store.mark_running("job-1", pid=1234, started_at="2026-07-26T00:00:01", db_connection=self.conn)
        self.assertFalse(won)
        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertEqual(job["status"], "cancelled")
        self.assertIsNone(job["pid"])


class TestFinalizeTerminalRaceSafety(JobStoreTestCase):
    """The core race-safety contract from JOB_LIFECYCLE.md: whichever of
    {subprocess exit, timeout handler, cancel} gets rowcount==1 wins; everyone
    else is a silent no-op."""

    def test_finalize_from_running_succeeds_once(self):
        self._insert(status="pending")
        job_store.mark_running("job-1", pid=1, started_at="t1", db_connection=self.conn)

        first = job_store.finalize_terminal(
            "job-1", status="succeeded", exit_code=0, finished_at="t2", db_connection=self.conn
        )
        self.assertTrue(first)

        second = job_store.finalize_terminal(
            "job-1", status="failed", exit_code=1, finished_at="t3", db_connection=self.conn
        )
        self.assertFalse(second)

        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["exit_code"], 0)
        self.assertEqual(job["finished_at"], "t2")

    def test_finalize_from_pending_noops(self):
        self._insert(status="pending")
        won = job_store.finalize_terminal("job-1", status="failed", exit_code=1, finished_at="t2", db_connection=self.conn)
        self.assertFalse(won)
        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertEqual(job["status"], "pending")

    def test_finalize_records_error_kind_and_quota_reset(self):
        self._insert(status="pending")
        job_store.mark_running("job-1", pid=1, started_at="t1", db_connection=self.conn)
        job_store.finalize_terminal(
            "job-1", status="failed", exit_code=1, finished_at="t2",
            error_message="Individual quota reached.", error_kind="quota_exhausted",
            quota_reset_at="2026-07-30T00:00:00", db_connection=self.conn,
        )
        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertEqual(job["error_kind"], "quota_exhausted")
        self.assertEqual(job["quota_reset_at"], "2026-07-30T00:00:00")


class TestForceFail(JobStoreTestCase):
    def test_force_fail_from_pending(self):
        self._insert(status="pending")
        won = job_store.force_fail("job-1", error_message="boom before spawn", finished_at="t2", db_connection=self.conn)
        self.assertTrue(won)
        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_message"], "boom before spawn")

    def test_force_fail_from_running(self):
        self._insert(status="pending")
        job_store.mark_running("job-1", pid=1, started_at="t1", db_connection=self.conn)
        won = job_store.force_fail("job-1", error_message="boom after spawn", finished_at="t2", db_connection=self.conn)
        self.assertTrue(won)
        self.assertEqual(job_store.get_job("job-1", db_connection=self.conn)["status"], "failed")

    def test_force_fail_noops_on_already_terminal(self):
        self._insert(status="pending")
        job_store.mark_running("job-1", pid=1, started_at="t1", db_connection=self.conn)
        job_store.finalize_terminal("job-1", status="succeeded", exit_code=0, finished_at="t2", db_connection=self.conn)
        won = job_store.force_fail("job-1", error_message="should not apply", finished_at="t3", db_connection=self.conn)
        self.assertFalse(won)
        self.assertEqual(job_store.get_job("job-1", db_connection=self.conn)["status"], "succeeded")


class TestMarkCancelledPending(JobStoreTestCase):
    def test_cancel_pending_succeeds(self):
        self._insert(status="pending")
        won = job_store.mark_cancelled_pending("job-1", finished_at="t2", db_connection=self.conn)
        self.assertTrue(won)
        self.assertEqual(job_store.get_job("job-1", db_connection=self.conn)["status"], "cancelled")

    def test_cancel_pending_noops_if_already_running(self):
        self._insert(status="pending")
        job_store.mark_running("job-1", pid=1, started_at="t1", db_connection=self.conn)
        won = job_store.mark_cancelled_pending("job-1", finished_at="t2", db_connection=self.conn)
        self.assertFalse(won)
        self.assertEqual(job_store.get_job("job-1", db_connection=self.conn)["status"], "running")


class TestMarkUnknownInterrupted(JobStoreTestCase):
    def test_marks_running_row(self):
        self._insert(status="pending")
        job_store.mark_running("job-1", pid=999999, started_at="t1", db_connection=self.conn)
        won = job_store.mark_unknown_interrupted(
            "job-1", error_message="pid 999999 not found at restart", finished_at="t2", db_connection=self.conn
        )
        self.assertTrue(won)
        job = job_store.get_job("job-1", db_connection=self.conn)
        self.assertEqual(job["status"], "unknown-interrupted")
        self.assertEqual(job["error_message"], "pid 999999 not found at restart")

    def test_noop_on_pending_row(self):
        self._insert(status="pending")
        won = job_store.mark_unknown_interrupted("job-1", error_message="x", finished_at="t2", db_connection=self.conn)
        self.assertFalse(won)


class TestListJobs(JobStoreTestCase):
    def test_filters_and_ordering(self):
        self._insert(job_id="job-1", context_id="ctx-a", status="succeeded", created_at="2026-07-26T00:00:01")
        self._insert(job_id="job-2", context_id="ctx-a", status="failed", created_at="2026-07-26T00:00:02")
        self._insert(job_id="job-3", context_id="ctx-b", status="succeeded", created_at="2026-07-26T00:00:03")

        all_jobs = job_store.list_jobs(db_connection=self.conn)
        self.assertEqual([j["job_id"] for j in all_jobs], ["job-3", "job-2", "job-1"])

        by_status = job_store.list_jobs(status_filter="succeeded", db_connection=self.conn)
        self.assertEqual([j["job_id"] for j in by_status], ["job-3", "job-1"])

        by_context = job_store.list_jobs(context_id="ctx-a", db_connection=self.conn)
        self.assertEqual([j["job_id"] for j in by_context], ["job-2", "job-1"])

    def test_provider_filter(self):
        self._insert(job_id="job-1", provider="agy", created_at="2026-07-26T00:00:01")
        self._insert(job_id="job-2", provider="cursor", created_at="2026-07-26T00:00:02")
        self._insert(job_id="job-3", provider="cursor", created_at="2026-07-26T00:00:03")

        by_provider = job_store.list_jobs(provider_filter="cursor", db_connection=self.conn)
        self.assertEqual([j["job_id"] for j in by_provider], ["job-3", "job-2"])

    def test_limit_applies(self):
        for i in range(5):
            self._insert(job_id=f"job-{i}", created_at=f"2026-07-26T00:00:0{i}")
        limited = job_store.list_jobs(limit=2, db_connection=self.conn)
        self.assertEqual(len(limited), 2)


class TestSweepOldTerminalJobs(JobStoreTestCase):
    def test_sweeps_only_old_terminal_rows(self):
        self._insert(job_id="job-old-succeeded", status="pending")
        job_store.mark_running("job-old-succeeded", pid=1, started_at="t1", db_connection=self.conn)
        job_store.finalize_terminal("job-old-succeeded", status="succeeded", exit_code=0, finished_at="2026-01-01T00:00:00", db_connection=self.conn)

        self._insert(job_id="job-recent", status="pending")
        job_store.mark_running("job-recent", pid=2, started_at="t1", db_connection=self.conn)
        job_store.finalize_terminal("job-recent", status="succeeded", exit_code=0, finished_at="2026-07-25T00:00:00", db_connection=self.conn)

        self._insert(job_id="job-still-pending", status="pending")
        self._insert(job_id="job-still-running", status="pending")
        job_store.mark_running("job-still-running", pid=3, started_at="t1", db_connection=self.conn)

        deleted = job_store.sweep_old_terminal_jobs("2026-07-01T00:00:00", db_connection=self.conn)

        self.assertIn("job-old-succeeded", deleted)
        self.assertNotIn("job-recent", deleted)
        self.assertNotIn("job-still-pending", deleted)
        self.assertNotIn("job-still-running", deleted)

        self.assertIsNone(job_store.get_job("job-old-succeeded", db_connection=self.conn))
        self.assertIsNotNone(job_store.get_job("job-recent", db_connection=self.conn))
        self.assertIsNotNone(job_store.get_job("job-still-pending", db_connection=self.conn))
        self.assertIsNotNone(job_store.get_job("job-still-running", db_connection=self.conn))


if __name__ == "__main__":
    unittest.main()
