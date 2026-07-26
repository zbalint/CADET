import os
import shutil
import tempfile
import unittest

from cadet.db.schema import init_db

EXPECTED_COLUMNS = {
    "job_id", "context_id", "label", "prompt_path", "cwd", "model", "effort",
    "skip_permissions", "pid", "status", "created_at", "started_at", "finished_at",
    "exit_code", "timeout_s", "stdout_log_path", "stderr_log_path", "error_message",
    "error_kind", "quota_reset_at",
}


class TestInitDb(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_jobs_table_with_expected_columns(self):
        conn = init_db(self.db_path)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs);").fetchall()}
            self.assertEqual(cols, EXPECTED_COLUMNS)
        finally:
            conn.close()

    def test_job_id_is_primary_key(self):
        conn = init_db(self.db_path)
        try:
            pk_cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs);").fetchall() if row[5] == 1]
            self.assertEqual(pk_cols, ["job_id"])
        finally:
            conn.close()

    def test_init_db_is_idempotent(self):
        conn1 = init_db(self.db_path)
        conn1.close()
        conn2 = init_db(self.db_path)
        try:
            cols = {row[1] for row in conn2.execute("PRAGMA table_info(jobs);").fetchall()}
            self.assertEqual(cols, EXPECTED_COLUMNS)
        finally:
            conn2.close()

    def test_wal_mode_enabled(self):
        conn = init_db(self.db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
