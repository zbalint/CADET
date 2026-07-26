import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from cadet.db import job_store
from cadet.db.schema import init_db
from cadet.jobs.dispatcher import Dispatcher
from cadet.web.app import create_app


class WebApiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        conn = init_db(self.db_path)
        conn.close()

        self.dispatcher = Dispatcher(agy_path="C:\\tools\\agy.exe", max_concurrent=2, db_path=self.db_path)
        self.app = create_app(self.dispatcher, self.db_path)
        self.client = TestClient(self.app)

    def tearDown(self):
        if self.dispatcher._dispatcher_task is not None:
            self.dispatcher._dispatcher_task.cancel()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _insert(self, job_id, status="pending", **overrides):
        job_dir = os.path.join(self.temp_dir, "logs", job_id)
        os.makedirs(job_dir, exist_ok=True)
        kwargs = dict(
            job_id=job_id, context_id="ctx-1", label="test-label", prompt_path=os.path.join(job_dir, "prompt.txt"),
            cwd=self.temp_dir, model=None, effort=None, skip_permissions=False,
            status=status, created_at="2026-07-26T00:00:00", timeout_s=30,
            stdout_log_path=os.path.join(job_dir, "stdout.log"),
            stderr_log_path=os.path.join(job_dir, "stderr.log"),
            db_path=self.db_path,
        )
        kwargs.update(overrides)
        job_store.insert_job(**kwargs)
        return job_dir


class TestHealth(WebApiTestCase):
    def test_health_ok(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


class TestListTasks(WebApiTestCase):
    def test_empty_list(self):
        resp = self.client.get("/api/tasks")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_returns_shaped_jobs(self):
        self._insert("job-1", context_id="ctx-a")
        self._insert("job-2", context_id="ctx-b")

        resp = self.client.get("/api/tasks")
        self.assertEqual(resp.status_code, 200)
        job_ids = {t["job_id"] for t in resp.json()}
        self.assertEqual(job_ids, {"job-1", "job-2"})

        resp = self.client.get("/api/tasks", params={"context_id": "ctx-a"})
        self.assertEqual([t["job_id"] for t in resp.json()], ["job-1"])

    def test_status_filter(self):
        self._insert("job-1", status="pending")
        self._insert("job-2")
        job_store.mark_running("job-2", pid=1, started_at="2026-07-26T00:00:00", db_path=self.db_path)
        job_store.finalize_terminal(
            "job-2", status="succeeded", exit_code=0,
            finished_at="2026-07-26T00:00:05", db_path=self.db_path,
        )

        resp = self.client.get("/api/tasks", params={"status_filter": "succeeded"})
        self.assertEqual([t["job_id"] for t in resp.json()], ["job-2"])

    def test_limit_clamped(self):
        self._insert("job-1")
        resp = self.client.get("/api/tasks", params={"limit": 99999})
        self.assertEqual(resp.status_code, 200)


class TestGetTask(WebApiTestCase):
    def test_unknown_job_404(self):
        resp = self.client.get("/api/tasks/does-not-exist")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("error", resp.json())

    def test_known_job_shaped(self):
        self._insert("job-1")
        resp = self.client.get("/api/tasks/job-1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_id"], "job-1")
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["queue_position"], 1)


class TestGetTaskOutput(WebApiTestCase):
    def test_unknown_job_404(self):
        resp = self.client.get("/api/tasks/does-not-exist/output")
        self.assertEqual(resp.status_code, 404)

    def test_reads_log_files(self):
        job_dir = self._insert("job-1")
        with open(os.path.join(job_dir, "stdout.log"), "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")

        resp = self.client.get("/api/tasks/job-1/output")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["stdout"], "line1\nline2\nline3\n")
        self.assertFalse(body["truncated"])

    def test_tail_lines_truncates(self):
        job_dir = self._insert("job-1")
        with open(os.path.join(job_dir, "stdout.log"), "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")

        resp = self.client.get("/api/tasks/job-1/output", params={"tail_lines": 1})
        body = resp.json()
        self.assertEqual(body["stdout"], "line3\n")
        self.assertTrue(body["truncated"])


class TestCancelTask(WebApiTestCase):
    def test_unknown_job_404(self):
        resp = self.client.post("/api/tasks/does-not-exist/cancel")
        self.assertEqual(resp.status_code, 404)

    def test_cancel_pending_job_flips_db_row(self):
        self._insert("job-1")
        resp = self.client.post("/api/tasks/job-1/cancel")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "cancelled")
        self.assertFalse(body["already_terminal"])

        job = job_store.get_job("job-1", db_path=self.db_path)
        self.assertEqual(job["status"], "cancelled")

    def test_cancel_idempotent_on_terminal_job(self):
        self._insert("job-1")
        job_store.mark_running("job-1", pid=1, started_at="2026-07-26T00:00:00", db_path=self.db_path)
        job_store.finalize_terminal(
            "job-1", status="succeeded", exit_code=0,
            finished_at="2026-07-26T00:00:05", db_path=self.db_path,
        )
        resp = self.client.post("/api/tasks/job-1/cancel")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["already_terminal"])
        self.assertEqual(body["status"], "succeeded")


class TestStaticFrontend(WebApiTestCase):
    def test_index_served_at_root(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("CADET", resp.text)

    def test_static_assets_served(self):
        resp = self.client.get("/static/app.js")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
