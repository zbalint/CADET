import os
import unittest
from unittest.mock import patch

from cadet.process.providers.copilot import build_argv, build_docker_argv

IMAGE = "cadet-copilot:latest"
PROMPT = "rendered prompt text"
CWD = "C:\\repos\\myapp"
TIMEOUT_S = 1800
JOB_ID = "job-abc123def456"
TOKEN = "copilot-test-token-123"


@patch.dict(os.environ, {}, clear=True)
class TestBuildDockerArgv(unittest.TestCase):
    """Tests the `docker run` wrapping of the copilot CLI (Phase 5), mirroring
    test_cursor_docker_argv.py's shape. The pure copilot-flag logic itself
    (build_argv) is covered separately in test_copilot_argv.py and reused
    here (with container=True) for the inner argv.

    Auth is UNCONFIRMED (see providers/copilot.py's module docstring) — two
    mechanisms are wired up, mirroring cursor's "auth volume primary, token
    optional override" shape. CADET_COPILOT_GITHUB_TOKEN is optional/
    forwarded when set, not required. No env vars are set by default here
    (clear=True), matching the "just the auth volume, no token" common case.
    """

    def test_docker_run_wrapping_shape(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertEqual(argv[0:5], ["docker", "run", "--rm", "--name", "cadet-copilot-job-abc123def456"])

    def test_workspace_bind_mount_uses_host_cwd_read_only_by_default(self):
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

    def test_auth_volume_always_mounted(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        joined = " ".join(argv)
        self.assertIn("cadet-copilot-auth:/root/.copilot", joined)

    def test_auth_volume_respects_env_override(self):
        with patch.dict(os.environ, {"CADET_COPILOT_AUTH_VOLUME": "custom-copilot-vol"}):
            argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertIn("custom-copilot-vol:/root/.copilot", " ".join(argv))

    def test_no_token_flag_when_unset(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        joined = " ".join(argv)
        self.assertNotIn("COPILOT_GITHUB_TOKEN", joined)

    def test_token_forwarded_as_env_var_when_set(self):
        with patch.dict(os.environ, {"CADET_COPILOT_GITHUB_TOKEN": TOKEN}):
            argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertIn("-e", argv)
        idx = argv.index("-e")
        self.assertEqual(argv[idx + 1], f"COPILOT_GITHUB_TOKEN={TOKEN}")

    def test_no_token_required_missing_does_not_raise(self):
        """The auth volume is the primary mechanism — an unset
        CADET_COPILOT_GITHUB_TOKEN must not raise, same as cursor's optional
        CURSOR_API_KEY."""
        build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)  # no exception

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

    def test_image_immediately_precedes_inner_copilot_argv(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index(IMAGE)
        self.assertEqual(argv[idx + 1], "copilot")
        self.assertEqual(argv[idx + 2], "-p")
        self.assertEqual(argv[idx + 3], PROMPT)

    def test_inner_argv_never_uses_node_exe_loader_indirection(self):
        """The critical Windows-host-building-Linux-argv bug: build_argv's
        own win32 node.exe/npm-loader.js resolution branch must never fire
        here, even though this function executes on the Windows host
        process. container=True forces that."""
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index(IMAGE)
        self.assertEqual(argv[idx + 1], "copilot")
        self.assertNotIn("npm-loader.js", " ".join(argv))

    def test_inner_dash_c_targets_container_path_not_host_cwd(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index("-C")
        self.assertEqual(argv[idx + 1], "/workspace")

    def test_resource_limits_respect_env_overrides(self):
        with patch.dict(os.environ, {
            "CADET_COPILOT_CONTAINER_MEMORY": "4g",
            "CADET_COPILOT_CONTAINER_CPUS": "4",
            "CADET_COPILOT_CONTAINER_PIDS_LIMIT": "1024",
        }):
            argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertEqual(argv[argv.index("--memory") + 1], "4g")
        self.assertEqual(argv[argv.index("--cpus") + 1], "4")
        self.assertEqual(argv[argv.index("--pids-limit") + 1], "1024")

    def test_model_effort_skip_permissions_forwarded_to_inner_argv(self):
        argv = build_docker_argv(
            IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID,
            model="gpt-5.4", effort="high", skip_permissions=True,
        )
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "gpt-5.4")
        idx = argv.index("--effort")
        self.assertEqual(argv[idx + 1], "high")
        self.assertNotIn("--mode", argv)
        self.assertNotIn("--deny-tool", argv)

    def test_sandbox_true_no_skip_permissions_gates_with_mode_plan_and_deny_shell(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID, sandbox=True, skip_permissions=False)
        idx = argv.index("--mode")
        self.assertEqual(argv[idx + 1], "plan")
        idx = argv.index("--deny-tool")
        self.assertEqual(argv[idx + 1], "shell")

    def test_allow_all_tools_always_present(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertIn("--allow-all-tools", argv)


class TestBuildArgvContainerFlag(unittest.TestCase):
    """container=True must always bypass the win32 node.exe/npm-loader.js
    resolution branch, on any host platform -- covered separately from
    TestBuildDockerArgv since this exercises build_argv directly, not
    through build_docker_argv."""

    def test_container_true_never_resolves_windows_node_loader(self):
        argv = build_argv("copilot", PROMPT, "/workspace", TIMEOUT_S, container=True)
        self.assertEqual(argv[0], "copilot")
        self.assertNotIn("npm-loader.js", " ".join(argv))


if __name__ == "__main__":
    unittest.main()
