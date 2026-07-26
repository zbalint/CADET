import os
import unittest
from unittest.mock import patch

from cadet.process.providers.codex import build_docker_argv

IMAGE = "cadet-codex:latest"
PROMPT = "rendered prompt text"
CWD = "C:\\repos\\myapp"
TIMEOUT_S = 1800
JOB_ID = "job-abc123def456"


@patch.dict(os.environ, {}, clear=True)
class TestBuildDockerArgv(unittest.TestCase):
    """Tests the `docker run` wrapping of the codex CLI, mirroring
    test_agy_docker_argv.py's TestBuildArgvDockerWrapping shape. The pure
    codex-flag logic itself (build_argv) is covered separately in
    test_codex_argv.py and reused unchanged here for the inner argv."""

    def test_docker_run_wrapping_shape(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertEqual(argv[0:5], ["docker", "run", "--rm", "--name", "cadet-codex-job-abc123def456"])

    def test_workspace_bind_mount_uses_host_cwd_read_only_by_default(self):
        """Default (skip_permissions=False, sandbox=True) mounts /workspace
        :ro -- codex's own internal sandbox can't run in this container (see
        module docstring), so the bind mount itself is the read-only
        enforcement now, not codex's own -s read-only flag."""
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index("-v")
        self.assertEqual(argv[idx + 1], f"{CWD}:/workspace:ro")
        self.assertIn("-w", argv)
        self.assertEqual(argv[argv.index("-w") + 1], "/workspace")

    def test_workspace_bind_mount_is_rw_when_skip_permissions(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID, skip_permissions=True)
        idx = argv.index("-v")
        self.assertEqual(argv[idx + 1], f"{CWD}:/workspace")

    def test_workspace_bind_mount_is_rw_when_sandbox_disabled(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID, sandbox=False)
        idx = argv.index("-v")
        self.assertEqual(argv[idx + 1], f"{CWD}:/workspace")

    def test_inner_codex_always_bypasses_its_own_sandbox(self):
        """codex's own bubblewrap-based sandbox cannot run in this container
        regardless of skip_permissions/sandbox -- those knobs now only steer
        the /workspace mount mode, while the inner codex process always gets
        --dangerously-bypass-approvals-and-sandbox so it can execute at all."""
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertNotIn("-s", argv)

    def test_auth_volume_mounted(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        joined = " ".join(argv)
        self.assertIn("cadet-codex-auth:/root/.codex", joined)

    def test_resource_and_hardening_flags_present(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertIn("--memory", argv)
        self.assertIn("--cpus", argv)
        self.assertIn("--pids-limit", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertIn("--security-opt=no-new-privileges", argv)

    def test_no_network_none_flag(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertNotIn("--network", argv)

    def test_image_immediately_precedes_inner_codex_argv(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index(IMAGE)
        self.assertEqual(argv[idx + 1], "codex")
        self.assertEqual(argv[idx + 2], "exec")
        self.assertEqual(argv[idx + 3], PROMPT)

    def test_inner_dash_c_targets_workspace_not_host_cwd(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index("-C")
        self.assertEqual(argv[idx + 1], "/workspace")

    def test_resource_limits_respect_env_overrides(self):
        with patch.dict(os.environ, {
            "CADET_CODEX_CONTAINER_MEMORY": "4g",
            "CADET_CODEX_CONTAINER_CPUS": "4",
            "CADET_CODEX_CONTAINER_PIDS_LIMIT": "1024",
            "CADET_CODEX_AUTH_VOLUME": "custom-codex-vol",
        }):
            argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertEqual(argv[argv.index("--memory") + 1], "4g")
        self.assertEqual(argv[argv.index("--cpus") + 1], "4")
        self.assertEqual(argv[argv.index("--pids-limit") + 1], "1024")
        self.assertIn("custom-codex-vol:/root/.codex", " ".join(argv))

    def test_model_effort_skip_permissions_forwarded_to_inner_argv(self):
        argv = build_docker_argv(
            IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID,
            model="gpt-5.6-terra", effort="high", skip_permissions=True,
        )
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-5.6-terra")
        self.assertIn("-c", argv)
        self.assertEqual(argv[argv.index("-c") + 1], "model_reasoning_effort=high")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)


if __name__ == "__main__":
    unittest.main()
