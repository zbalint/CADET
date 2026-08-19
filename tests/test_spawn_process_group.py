"""Regression coverage for the host-crash bug: a spawned provider process
must be detached into its own session/process group (start_new_session=True)
so treekill.kill_process_tree's os.killpg() can never reach CADET's own
server process (and, since CADET itself runs as an undetached stdio child,
the host Claude Code process) on cancel or timeout.

Without start_new_session=True, the spawned child inherits CADET server's
process group, and kill_process_tree's os.killpg(os.getpgid(pid), SIGTERM)
fans out to that whole group instead of just the job's own tree.
"""
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from cadet.process.launcher import spawn_agy
from cadet.process.providers import codex, cursor, copilot


class TestSpawnStartsNewSession(unittest.IsolatedAsyncioTestCase):
    async def test_spawn_agy_uses_start_new_session(self):
        with tempfile.TemporaryFile() as stdout_fh, tempfile.TemporaryFile() as stderr_fh:
            with patch(
                "cadet.process.launcher.asyncio.create_subprocess_exec", AsyncMock()
            ) as mock_exec:
                await spawn_agy(
                    "cadet-agy:latest", "do the thing", "/workspace", 60,
                    stdout_fh, stderr_fh, job_id="job-1",
                )
                self.assertTrue(mock_exec.await_args.kwargs.get("start_new_session"))

    async def test_spawn_codex_uses_start_new_session(self):
        with tempfile.TemporaryFile() as stdout_fh, tempfile.TemporaryFile() as stderr_fh:
            with patch(
                "cadet.process.providers.codex.asyncio.create_subprocess_exec", AsyncMock()
            ) as mock_exec:
                await codex.spawn(
                    "cadet-codex:latest", "do the thing", "/workspace", 60,
                    stdout_fh, stderr_fh, job_id="job-1",
                )
                self.assertTrue(mock_exec.await_args.kwargs.get("start_new_session"))

    async def test_spawn_cursor_uses_start_new_session(self):
        with tempfile.TemporaryFile() as stdout_fh, tempfile.TemporaryFile() as stderr_fh:
            with patch(
                "cadet.process.providers.cursor.asyncio.create_subprocess_exec", AsyncMock()
            ) as mock_exec:
                await cursor.spawn(
                    "cadet-cursor:latest", "do the thing", "/workspace", 60,
                    stdout_fh, stderr_fh, job_id="job-1",
                )
                self.assertTrue(mock_exec.await_args.kwargs.get("start_new_session"))

    async def test_spawn_copilot_uses_start_new_session(self):
        with tempfile.TemporaryFile() as stdout_fh, tempfile.TemporaryFile() as stderr_fh:
            with patch(
                "cadet.process.providers.copilot.asyncio.create_subprocess_exec", AsyncMock()
            ) as mock_exec:
                await copilot.spawn(
                    "cadet-copilot:latest", "do the thing", "/workspace", 60,
                    stdout_fh, stderr_fh, job_id="job-1",
                )
                self.assertTrue(mock_exec.await_args.kwargs.get("start_new_session"))


if __name__ == "__main__":
    unittest.main()
