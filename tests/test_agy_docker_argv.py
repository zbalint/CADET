import os
import unittest
from unittest.mock import patch

from cadet.process.launcher import build_argv, container_name_for_job

IMAGE = "cadet-agy:latest"
PROMPT = "rendered prompt text"
CWD = "C:\\repos\\myapp"
TIMEOUT_S = 1800
JOB_ID = "job-abc123def456"


class TestContainerNameForJob(unittest.TestCase):
    def test_deterministic_and_docker_name_safe(self):
        name = container_name_for_job(JOB_ID)
        self.assertEqual(name, "cadet-agy-job-abc123def456")
        # Docker container names must match ^[a-zA-Z0-9][a-zA-Z0-9_.-]+$
        self.assertRegex(name, r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$")

    def test_different_job_ids_produce_different_names(self):
        self.assertNotEqual(container_name_for_job("job-aaa"), container_name_for_job("job-bbb"))


@patch.dict(os.environ, {}, clear=True)
class TestBuildArgvDockerWrapping(unittest.TestCase):
    def test_docker_run_wrapping_shape(self):
        argv = build_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertEqual(argv[0:5], ["docker", "run", "--rm", "--name", "cadet-agy-job-abc123def456"])

    def test_workspace_bind_mount_uses_host_cwd(self):
        argv = build_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index("-v")
        self.assertEqual(argv[idx + 1], f"{CWD}:/workspace")
        self.assertIn("-w", argv)
        self.assertEqual(argv[argv.index("-w") + 1], "/workspace")

    def test_gemini_volume_mounted(self):
        argv = build_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        joined = " ".join(argv)
        self.assertIn("cadet-agy-gemini:/root/.gemini", joined)

    def test_resource_and_hardening_flags_present(self):
        argv = build_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertIn("--memory", argv)
        self.assertIn("--cpus", argv)
        self.assertIn("--pids-limit", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertIn("--security-opt=no-new-privileges", argv)

    def test_no_network_none_flag(self):
        argv = build_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertNotIn("--network", argv)

    def test_image_immediately_precedes_inner_agy_argv(self):
        argv = build_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index(IMAGE)
        self.assertEqual(argv[idx + 1], "agy")
        self.assertEqual(argv[idx + 2], "-p")
        self.assertEqual(argv[idx + 3], PROMPT)

    def test_inner_add_dir_targets_workspace_not_host_cwd(self):
        argv = build_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index("--add-dir")
        self.assertEqual(argv[idx + 1], "/workspace")

    def test_resource_limits_respect_env_overrides(self):
        with patch.dict(os.environ, {
            "CADET_AGY_CONTAINER_MEMORY": "4g",
            "CADET_AGY_CONTAINER_CPUS": "4",
            "CADET_AGY_CONTAINER_PIDS_LIMIT": "1024",
            "CADET_AGY_GEMINI_VOLUME": "custom-gemini-vol",
        }):
            argv = build_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertEqual(argv[argv.index("--memory") + 1], "4g")
        self.assertEqual(argv[argv.index("--cpus") + 1], "4")
        self.assertEqual(argv[argv.index("--pids-limit") + 1], "1024")
        self.assertIn("custom-gemini-vol:/root/.gemini", " ".join(argv))


if __name__ == "__main__":
    unittest.main()
