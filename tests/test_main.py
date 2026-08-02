import signal
import subprocess
import sys
import time
import unittest


class TestMainSignalHandling(unittest.TestCase):
    def test_main_exits_zero_on_sigterm(self) -> None:
        """Verify that sending SIGTERM to cadet.__main__ causes a clean exit (returncode 0)."""
        import os
        env = dict(os.environ)
        env["PYTHONPATH"] = "src" + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen(
            [sys.executable, "-m", "cadet.__main__"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(2)
        proc.send_signal(signal.SIGTERM)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.fail("cadet.__main__ process timed out without exiting after SIGTERM")

        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
