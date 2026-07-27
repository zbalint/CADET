import os
import shutil
import tempfile
import unittest

from cadet.db import provider_status_store
from cadet.db.schema import init_db


class TestProviderStatusStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        init_db(self.db_path).close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_status_returns_none_when_nothing_recorded(self):
        self.assertIsNone(provider_status_store.get_status("cursor", db_path=self.db_path))

    def test_upsert_then_get_round_trip(self):
        provider_status_store.upsert_exhaustion(
            "cursor", "2026-08-26T00:00:00", "confirmed", "job-1", "2026-07-27T12:00:00",
            db_path=self.db_path,
        )
        row = provider_status_store.get_status("cursor", db_path=self.db_path)
        self.assertEqual(row["pool_key"], "cursor")
        self.assertEqual(row["quota_reset_at"], "2026-08-26T00:00:00")
        self.assertEqual(row["confidence"], "confirmed")
        self.assertEqual(row["source_job_id"], "job-1")

    def test_upsert_overwrites_existing_row_for_same_pool_key(self):
        provider_status_store.upsert_exhaustion(
            "cursor", "2026-08-01T00:00:00", "estimated", "job-1", "2026-07-01T00:00:00",
            db_path=self.db_path,
        )
        provider_status_store.upsert_exhaustion(
            "cursor", "2026-08-26T00:00:00", "confirmed", "job-2", "2026-07-27T12:00:00",
            db_path=self.db_path,
        )
        row = provider_status_store.get_status("cursor", db_path=self.db_path)
        self.assertEqual(row["quota_reset_at"], "2026-08-26T00:00:00")
        self.assertEqual(row["confidence"], "confirmed")
        self.assertEqual(row["source_job_id"], "job-2")

    def test_upsert_is_isolated_per_pool_key(self):
        provider_status_store.upsert_exhaustion(
            "agy:gemini", "2026-07-30T00:00:00", "confirmed", "job-1", "2026-07-27T00:00:00",
            db_path=self.db_path,
        )
        provider_status_store.upsert_exhaustion(
            "agy:claude_gpt", "2026-07-31T00:00:00", "confirmed", "job-2", "2026-07-27T00:00:00",
            db_path=self.db_path,
        )
        gemini = provider_status_store.get_status("agy:gemini", db_path=self.db_path)
        claude_gpt = provider_status_store.get_status("agy:claude_gpt", db_path=self.db_path)
        self.assertEqual(gemini["quota_reset_at"], "2026-07-30T00:00:00")
        self.assertEqual(claude_gpt["quota_reset_at"], "2026-07-31T00:00:00")

    def test_clear_status_removes_row(self):
        provider_status_store.upsert_exhaustion(
            "codex", "2026-08-03T00:00:00", "confirmed", "job-1", "2026-07-27T00:00:00",
            db_path=self.db_path,
        )
        provider_status_store.clear_status("codex", db_path=self.db_path)
        self.assertIsNone(provider_status_store.get_status("codex", db_path=self.db_path))

    def test_clear_status_on_missing_row_is_a_no_op(self):
        provider_status_store.clear_status("codex", db_path=self.db_path)  # should not raise


if __name__ == "__main__":
    unittest.main()
