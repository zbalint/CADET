import os
import shutil
import sqlite3
import tempfile
import unittest

from cadet.db.schema import init_db

EXPECTED_COLUMNS = {
    "job_id", "context_id", "label", "prompt_path", "cwd", "provider", "model", "effort",
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

    def test_migration_adds_provider_column_to_pre_existing_db(self):
        # Simulate a DB created before the `provider` column existed: build the
        # jobs table by hand without it, insert a row, then run init_db() again
        # and confirm the column appears with existing rows backfilled to 'agy'.
        pre_migration_columns = EXPECTED_COLUMNS - {"provider"}
        conn = sqlite3.connect(self.db_path)
        try:
            cols_sql = ", ".join(f"{c} TEXT" for c in pre_migration_columns if c != "job_id")
            conn.execute(f"CREATE TABLE jobs (job_id TEXT PRIMARY KEY, {cols_sql})")
            conn.execute(
                "INSERT INTO jobs (job_id, context_id, prompt_path, cwd, status, created_at, "
                "timeout_s, stdout_log_path, stderr_log_path) VALUES "
                "('job-1', 'ctx-1', 'p.txt', 'C:\\\\x', 'succeeded', 't1', '30', 'o.log', 'e.log')"
            )
            conn.commit()
        finally:
            conn.close()

        conn = init_db(self.db_path)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs);").fetchall()}
            self.assertEqual(cols, EXPECTED_COLUMNS)
            row = conn.execute("SELECT provider FROM jobs WHERE job_id = 'job-1'").fetchone()
            self.assertEqual(row[0], "agy")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
