import subprocess
import unittest
from unittest.mock import MagicMock, patch

from cadet.process import treekill


class TestKillProcessTreeWindows(unittest.TestCase):
    @patch("cadet.process.treekill.platform.system", return_value="Windows")
    @patch("cadet.process.treekill.subprocess.run")
    def test_calls_taskkill_with_correct_args(self, mock_run, mock_system):
        mock_run.return_value = MagicMock(returncode=0)
        treekill.kill_process_tree(4321)
        mock_run.assert_called_once_with(
            ["taskkill", "/PID", "4321", "/T", "/F"],
            capture_output=True, text=True, timeout=10,
        )

    @patch("cadet.process.treekill.platform.system", return_value="Windows")
    @patch("cadet.process.treekill.subprocess.run")
    def test_process_not_found_does_not_raise(self, mock_run, mock_system):
        mock_run.return_value = MagicMock(returncode=128)
        treekill.kill_process_tree(4321)  # should not raise

    @patch("cadet.process.treekill.platform.system", return_value="Windows")
    @patch("cadet.process.treekill.subprocess.run")
    def test_unexpected_returncode_does_not_raise(self, mock_run, mock_system):
        mock_run.return_value = MagicMock(returncode=1)
        treekill.kill_process_tree(4321)  # best-effort — never raises

    @patch("cadet.process.treekill.platform.system", return_value="Windows")
    @patch("cadet.process.treekill.subprocess.run")
    def test_timeout_expired_does_not_raise(self, mock_run, mock_system):
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["taskkill", "/PID", "4321", "/T", "/F"], timeout=10
        )
        treekill.kill_process_tree(4321)  # best-effort — never raises


class TestKillProcessTreePosix(unittest.TestCase):
    @patch("cadet.process.treekill.time.sleep")
    @patch("cadet.process.treekill.os.killpg", create=True)
    @patch("cadet.process.treekill.os.getpgid", create=True, return_value=555)
    @patch("cadet.process.treekill.platform.system", return_value="Linux")
    def test_sends_sigterm_then_sigkill_after_grace_period(self, mock_system, mock_getpgid, mock_killpg, mock_sleep):
        treekill.kill_process_tree(9999)
        self.assertEqual(mock_killpg.call_count, 2)
        first_call, second_call = mock_killpg.call_args_list
        self.assertEqual(first_call.args, (555, treekill.signal.SIGTERM))
        expected_sigkill = getattr(treekill.signal, "SIGKILL", treekill.signal.SIGTERM)
        self.assertEqual(second_call.args, (555, expected_sigkill))
        mock_sleep.assert_called_once()

    @patch("cadet.process.treekill.os.getpgid", create=True, side_effect=ProcessLookupError)
    @patch("cadet.process.treekill.platform.system", return_value="Linux")
    def test_missing_process_noops(self, mock_system, mock_getpgid):
        treekill.kill_process_tree(9999)  # should not raise


if __name__ == "__main__":
    unittest.main()
