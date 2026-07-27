import os
import shutil
import tempfile
import unittest

from cadet import status
from cadet.db import job_store
from cadet.db.schema import init_db


class StatusTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = init_db(self.db_path)
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _insert(self, job_id, status_="pending", **overrides):
        kwargs = dict(
            job_id=job_id, context_id="ctx-1", label="test", prompt_path="p.txt",
            cwd=self.temp_dir, model=None, effort=None, skip_permissions=False,
            status=status_, created_at="2026-07-26T00:00:00", timeout_s=30,
            stdout_log_path=os.path.join(self.temp_dir, "stdout.log"),
            stderr_log_path=os.path.join(self.temp_dir, "stderr.log"),
            db_path=self.db_path,
        )
        kwargs.update(overrides)
        job_store.insert_job(**kwargs)


class TestPendingQueuePosition(StatusTestCase):
    def test_position_reflects_created_at_order(self):
        self._insert("job-1", created_at="2026-07-26T00:00:00")
        self._insert("job-2", created_at="2026-07-26T00:00:01")
        self._insert("job-3", created_at="2026-07-26T00:00:02")

        self.assertEqual(status.pending_queue_position("job-1", db_path=self.db_path), 1)
        self.assertEqual(status.pending_queue_position("job-2", db_path=self.db_path), 2)
        self.assertEqual(status.pending_queue_position("job-3", db_path=self.db_path), 3)

    def test_unknown_job_returns_none(self):
        self.assertIsNone(status.pending_queue_position("job-nope", db_path=self.db_path))


class TestShapeStatusDict(StatusTestCase):
    def test_pending_job_has_queue_position_and_no_elapsed(self):
        self._insert("job-1")
        job = job_store.get_job("job-1", db_path=self.db_path)
        shaped = status.shape_status_dict(job, db_path=self.db_path)
        self.assertEqual(shaped["status"], "pending")
        self.assertEqual(shaped["queue_position"], 1)
        self.assertIsNone(shaped["elapsed_s"])

    def test_finished_job_has_elapsed_and_no_queue_position(self):
        self._insert("job-1")
        job_store.mark_running("job-1", pid=123, started_at="2026-07-26T00:00:00", db_path=self.db_path)
        job_store.finalize_terminal(
            "job-1", status="succeeded", exit_code=0,
            finished_at="2026-07-26T00:00:05", db_path=self.db_path,
        )
        job = job_store.get_job("job-1", db_path=self.db_path)
        shaped = status.shape_status_dict(job, db_path=self.db_path)
        self.assertEqual(shaped["status"], "succeeded")
        self.assertIsNone(shaped["queue_position"])
        self.assertEqual(shaped["elapsed_s"], 5.0)

    def test_expected_keys_present(self):
        self._insert("job-1")
        job = job_store.get_job("job-1", db_path=self.db_path)
        shaped = status.shape_status_dict(job, db_path=self.db_path)
        expected_keys = {
            "job_id", "label", "context_id", "status", "provider", "model", "effort",
            "skip_permissions", "skip_quota_check", "created_at", "started_at", "elapsed_s", "exit_code",
            "error_kind", "quota_reset_at", "quota_reset_confidence", "timeout_s", "queue_position",
        }
        self.assertEqual(set(shaped.keys()), expected_keys)

    def test_effort_and_skip_permissions_round_trip(self):
        self._insert("job-1", effort="high", skip_permissions=True)
        job = job_store.get_job("job-1", db_path=self.db_path)
        shaped = status.shape_status_dict(job, db_path=self.db_path)
        self.assertEqual(shaped["effort"], "high")
        self.assertIs(shaped["skip_permissions"], True)


class TestReadLog(StatusTestCase):
    def test_missing_file_returns_empty_untruncated(self):
        text, truncated = status.read_log(os.path.join(self.temp_dir, "nope.log"))
        self.assertEqual(text, "")
        self.assertFalse(truncated)

    def test_reads_full_file_when_no_tail(self):
        path = os.path.join(self.temp_dir, "out.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("a\nb\nc\n")
        text, truncated = status.read_log(path)
        self.assertEqual(text, "a\nb\nc\n")
        self.assertFalse(truncated)

    def test_tail_lines_truncates(self):
        path = os.path.join(self.temp_dir, "out.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("a\nb\nc\n")
        text, truncated = status.read_log(path, tail_lines=1)
        self.assertEqual(text, "c\n")
        self.assertTrue(truncated)


if __name__ == "__main__":
    unittest.main()
