import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cadet import config
from cadet.db import job_store, provider_status_store
from cadet.db.schema import init_db
from cadet.jobs.dispatcher import Dispatcher
from cadet.mcp import tools

_ENV_VARS = ["CADET_STATE_DIR", "CADET_AGY_PATH", "CADET_DEFAULT_CWD", "CADET_MAX_TIMEOUT_S"]


class McpToolsTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved_env = {var: os.environ.get(var) for var in _ENV_VARS}
        self.temp_dir = tempfile.mkdtemp()
        self.scratch_cwd = os.path.join(self.temp_dir, "scratch")
        os.makedirs(self.scratch_cwd, exist_ok=True)

        os.environ["CADET_STATE_DIR"] = self.temp_dir
        fake_agy = os.path.join(self.temp_dir, "agy.exe")
        with open(fake_agy, "w") as f:
            f.write("")
        os.environ["CADET_AGY_PATH"] = fake_agy

        self.db_path = config.get_db_path()
        conn = init_db(self.db_path)
        conn.close()

        self.dispatcher = Dispatcher(executable_paths={"agy": fake_agy}, max_concurrent=2, db_path=self.db_path)
        tools.set_dispatcher(self.dispatcher)

    def tearDown(self):
        if self.dispatcher._dispatcher_task is not None:
            self.dispatcher._dispatcher_task.cancel()
        tools.set_dispatcher(None)
        for var, val in self._saved_env.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class TestDelegateTask(McpToolsTestCase):
    async def test_missing_prompt_returns_error(self):
        result = await tools.delegate_task(cwd=self.scratch_cwd)
        self.assertIn("error", result)

    async def test_invalid_cwd_returns_error_and_creates_no_job(self):
        result = await tools.delegate_task(prompt="do something", cwd=os.path.join(self.temp_dir, "nope"))
        self.assertIn("error", result)
        self.assertEqual(len(tools.list_tasks()), 0)

    async def test_successful_delegation_creates_pending_job(self):
        result = await tools.delegate_task(prompt="do the thing", cwd=self.scratch_cwd, label="my-label")

        self.assertNotIn("error", result)
        self.assertTrue(result["job_id"].startswith("job-"))
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["label"], "my-label")
        self.assertTrue(result["context_id"].startswith("cadet-"))
        self.assertEqual(result["queue_position"], 1)

        job = tools.check_task_status(job_id=result["job_id"])
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["context_id"], result["context_id"])

    async def test_prompt_txt_is_written_with_rendered_template(self):
        result = await tools.delegate_task(
            prompt="Refactor the auth middleware", cwd=self.scratch_cwd,
            context_id="ctx-test", label="my-label",
        )
        job_id = result["job_id"]
        prompt_path = os.path.join(config.get_logs_dir(), job_id, "prompt.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            rendered = f.read()
        self.assertIn("Context ID: `ctx-test`", rendered)
        self.assertIn(f"Job label: my-label", rendered)
        self.assertIn(f"Working directory: {self.scratch_cwd}", rendered)
        self.assertIn(f"CADET job id: {job_id}", rendered)
        self.assertIn("Refactor the auth middleware", rendered)

    async def test_context_id_alias_via_kwargs(self):
        result = await tools.delegate_task(
            prompt="x", cwd=self.scratch_cwd, kwargs={"project_id": "ctx-alias"},
        )
        self.assertEqual(result["context_id"], "ctx-alias")

    async def test_timeout_is_clamped_to_configured_max(self):
        os.environ["CADET_MAX_TIMEOUT_S"] = "100"
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd, timeout_s=99999)
        job = tools.check_task_status(job_id=result["job_id"])
        self.assertEqual(job["timeout_s"], 100)

    async def test_defaults_to_agy_provider(self):
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd)
        job = tools.check_task_status(job_id=result["job_id"])
        self.assertEqual(job["provider"], "agy")

    async def test_unknown_provider_returns_error_and_creates_no_job(self):
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd, provider="bogus")
        self.assertIn("error", result)
        self.assertEqual(len(tools.list_tasks()), 0)

    async def test_dispatch_loop_running_reflects_in_return_status(self):
        release_event = asyncio.Event()

        async def fake_spawn_agy(agy_path, prompt_text, cwd, timeout_s, stdout_fh, stderr_fh, **kwargs):
            proc = MagicMock()
            proc.pid = 9999

            async def wait():
                await release_event.wait()
                return 0

            proc.wait = wait
            return proc

        with patch("cadet.jobs.dispatcher.spawn_agy", fake_spawn_agy):
            self.dispatcher.start()
            result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd)
            self.assertEqual(result["status"], "running")
            self.assertIsNone(result["queue_position"])
            release_event.set()
            await asyncio.sleep(0.05)


class TestDelegateTaskQuotaGate(McpToolsTestCase):
    async def test_fast_path_blocks_without_creating_a_job(self):
        provider_status_store.upsert_exhaustion(
            "agy:model:none", "2099-01-01T00:00:00", "confirmed", "job-0", "2026-07-27T00:00:00",
            db_path=self.db_path,
        )
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd)

        self.assertEqual(result["error_kind"], "quota_exhausted")
        self.assertEqual(result["quota_reset_at"], "2099-01-01T00:00:00")
        self.assertEqual(result["quota_reset_confidence"], "confirmed")
        self.assertEqual(len(tools.list_tasks()), 0)

    async def test_skip_quota_check_bypasses_fast_path(self):
        provider_status_store.upsert_exhaustion(
            "agy:model:none", "2099-01-01T00:00:00", "confirmed", "job-0", "2026-07-27T00:00:00",
            db_path=self.db_path,
        )
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd, skip_quota_check=True)

        self.assertNotIn("error", result)
        self.assertEqual(result["status"], "pending")
        # Regression: skip_quota_check_ must actually be persisted on the job row,
        # not just used for the synchronous fast-path above -- otherwise the
        # dispatcher-side gate in run_job (which reads job["skip_quota_check"]
        # back from storage) re-blocks the job anyway, silently defeating the
        # escape hatch this test name promises.
        job = job_store.get_job(result["job_id"], db_path=self.db_path)
        self.assertEqual(job["skip_quota_check"], 1)


class TestCheckTaskStatus(McpToolsTestCase):
    async def test_missing_job_id_returns_error(self):
        self.assertIn("error", tools.check_task_status())

    async def test_unknown_job_returns_error(self):
        self.assertIn("error", tools.check_task_status(job_id="job-does-not-exist"))

    async def test_alias_job_id_via_kwargs(self):
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd)
        status = tools.check_task_status(kwargs={"job_id": result["job_id"]})
        self.assertEqual(status["job_id"], result["job_id"])


class TestGetTaskOutput(McpToolsTestCase):
    async def test_reads_log_files(self):
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd)
        job_id = result["job_id"]
        job = tools.check_task_status(job_id=job_id)

        stdout_path = os.path.join(config.get_logs_dir(), job_id, "stdout.log")
        with open(stdout_path, "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")

        output = tools.get_task_output(job_id=job_id)
        self.assertEqual(output["stdout"], "line1\nline2\nline3\n")
        self.assertFalse(output["truncated"])
        self.assertEqual(output["stdout_log_path"], stdout_path)

    async def test_tail_lines_truncates(self):
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd)
        job_id = result["job_id"]
        stdout_path = os.path.join(config.get_logs_dir(), job_id, "stdout.log")
        with open(stdout_path, "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")

        output = tools.get_task_output(job_id=job_id, tail_lines=1)
        self.assertEqual(output["stdout"], "line3\n")
        self.assertTrue(output["truncated"])

    async def test_unknown_job_returns_error(self):
        self.assertIn("error", tools.get_task_output(job_id="nope"))


class TestListTasks(McpToolsTestCase):
    async def test_returns_list_shaped_like_check_task_status(self):
        r1 = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd, context_id="ctx-a")
        r2 = await tools.delegate_task(prompt="y", cwd=self.scratch_cwd, context_id="ctx-b")

        all_tasks = tools.list_tasks()
        self.assertEqual(len(all_tasks), 2)
        self.assertEqual({t["job_id"] for t in all_tasks}, {r1["job_id"], r2["job_id"]})

        filtered = tools.list_tasks(context_id="ctx-a")
        self.assertEqual([t["job_id"] for t in filtered], [r1["job_id"]])

    async def test_limit_hard_capped_at_200(self):
        result = tools.list_tasks(limit=99999)
        self.assertIsInstance(result, list)  # would raise if limit weren't clamped safely

    async def test_provider_filter_and_alias(self):
        await tools.delegate_task(prompt="x", cwd=self.scratch_cwd, context_id="ctx-agy")
        job_store.insert_job(
            job_id="job-cursor", context_id="ctx-cursor", label="l", prompt_path="p.txt",
            cwd=self.scratch_cwd, model=None, effort=None, skip_permissions=False,
            provider="cursor", status="pending", created_at="2026-07-26T00:00:00", timeout_s=30,
            stdout_log_path="out.log", stderr_log_path="err.log", db_path=self.db_path,
        )

        by_provider_filter = tools.list_tasks(provider_filter="cursor")
        self.assertEqual([t["job_id"] for t in by_provider_filter], ["job-cursor"])

        by_alias = tools.list_tasks(provider="cursor")
        self.assertEqual([t["job_id"] for t in by_alias], ["job-cursor"])


class TestCancelTask(McpToolsTestCase):
    async def test_missing_job_id_returns_error(self):
        self.assertIn("error", await tools.cancel_task())

    async def test_unknown_job_returns_error(self):
        self.assertIn("error", await tools.cancel_task(job_id="nope"))

    async def test_cancel_pending_job(self):
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd)
        job_id = result["job_id"]

        cancel_result = await tools.cancel_task(job_id=job_id)
        self.assertEqual(cancel_result["status"], "cancelled")
        self.assertFalse(cancel_result["already_terminal"])

        status = tools.check_task_status(job_id=job_id)
        self.assertEqual(status["status"], "cancelled")

    async def test_cancel_is_idempotent_on_terminal_job(self):
        result = await tools.delegate_task(prompt="x", cwd=self.scratch_cwd)
        job_id = result["job_id"]
        await tools.cancel_task(job_id=job_id)

        second = await tools.cancel_task(job_id=job_id)
        self.assertTrue(second["already_terminal"])
        self.assertEqual(second["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
