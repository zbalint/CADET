import os
import unittest
from unittest.mock import patch

from cadet.process.providers.cursor import _WINDOWS_POWERSHELL, build_argv, build_docker_argv

IMAGE = "cadet-cursor:latest"
PROMPT = "rendered prompt text"
CWD = "C:\\repos\\myapp"
TIMEOUT_S = 1800
JOB_ID = "job-abc123def456"
API_KEY = "cursor-test-key-123"


@patch.dict(os.environ, {}, clear=True)
class TestBuildDockerArgv(unittest.TestCase):
    """Tests the `docker run` wrapping of the cursor CLI (Phase 4), mirroring
    test_codex_docker_argv.py's shape. The pure cursor-flag logic itself
    (build_argv) is covered separately in test_cursor_argv.py and reused here
    (with container=True) for the inner argv.

    Auth is a Docker named volume (OAuth-authenticated, see
    providers/cursor.py's build_docker_argv docstring), same shape as
    agy/codex — CADET_CURSOR_API_KEY is optional/forwarded when set, not
    required. No env vars are set by default here (clear=True), matching the
    real "just the auth volume, no API key" common case."""

    def test_docker_run_wrapping_shape(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertEqual(argv[0:5], ["docker", "run", "--rm", "--name", "cadet-cursor-job-abc123def456"])

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
        self.assertIn("cadet-cursor-auth:/root/.config/cursor", joined)

    def test_auth_volume_respects_env_override(self):
        with patch.dict(os.environ, {"CADET_CURSOR_AUTH_VOLUME": "custom-cursor-vol"}):
            argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertIn("custom-cursor-vol:/root/.config/cursor", " ".join(argv))

    def test_no_api_key_flag_when_unset(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        joined = " ".join(argv)
        self.assertNotIn("CURSOR_API_KEY", joined)

    def test_api_key_forwarded_as_env_var_when_set(self):
        with patch.dict(os.environ, {"CADET_CURSOR_API_KEY": API_KEY}):
            argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        target = f"CURSOR_API_KEY={API_KEY}"
        self.assertIn(target, argv)
        idx = argv.index(target)
        self.assertEqual(argv[idx - 1], "-e")

    def test_no_api_key_required_missing_does_not_raise(self):
        """Unlike the initial (unverified) API-key-only design, the auth
        volume is the primary mechanism now — an unset CADET_CURSOR_API_KEY
        must not raise."""
        build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)  # no exception

    def test_resource_and_hardening_flags_present(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertIn("--memory", argv)
        self.assertIn("--cpus", argv)
        self.assertIn("--pids-limit", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertIn("--security-opt=no-new-privileges", argv)

    def test_host_uid_gid_flags_present(self):
        """docker_user_flags() -- see test_docker_user_flags.py -- lets
        entrypoint.sh drop from root to the host UID/GID so writes to the
        bind-mounted /workspace actually land despite --cap-drop=ALL."""
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        joined = " ".join(argv)
        self.assertIn("--cap-add=CHOWN", argv)
        self.assertIn("--cap-add=SETUID", argv)
        self.assertIn("--cap-add=SETGID", argv)
        self.assertIn("HOST_UID=", joined)
        self.assertIn("HOST_GID=", joined)

    def test_no_network_none_flag(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertNotIn("--network", argv)

    def test_image_immediately_precedes_inner_agent_argv(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index(IMAGE)
        self.assertEqual(argv[idx + 1], "agent")
        self.assertEqual(argv[idx + 2], "-p")
        self.assertEqual(argv[idx + 3], PROMPT)

    def test_inner_argv_never_wrapped_in_powershell_regardless_of_host_platform(self):
        """The critical Windows-host-building-Linux-argv bug: build_argv's
        own win32 branch must never fire here, even though this function
        executes on the Windows host process. container=True forces that."""
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertNotIn(_WINDOWS_POWERSHELL, argv)
        idx = argv.index(IMAGE)
        self.assertEqual(argv[idx + 1], "agent")

    def test_inner_dash_dash_workspace_targets_container_path_not_host_cwd(self):
        argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        idx = argv.index("--workspace")
        self.assertEqual(argv[idx + 1], "/workspace")

    def test_resource_limits_respect_env_overrides(self):
        with patch.dict(os.environ, {
            "CADET_CURSOR_CONTAINER_MEMORY": "4g",
            "CADET_CURSOR_CONTAINER_CPUS": "4",
            "CADET_CURSOR_CONTAINER_PIDS_LIMIT": "1024",
        }):
            argv = build_docker_argv(IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID)
        self.assertEqual(argv[argv.index("--memory") + 1], "4g")
        self.assertEqual(argv[argv.index("--cpus") + 1], "4")
        self.assertEqual(argv[argv.index("--pids-limit") + 1], "1024")

    def test_model_effort_skip_permissions_forwarded_to_inner_argv(self):
        argv = build_docker_argv(
            IMAGE, PROMPT, CWD, TIMEOUT_S, JOB_ID,
            model="claude-opus-4-8", effort="high", skip_permissions=True,
        )
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "claude-opus-4-8[effort=high]")
        self.assertIn("--force", argv)
        self.assertNotIn("--mode", argv)


class TestBuildArgvContainerFlag(unittest.TestCase):
    """container=True must always bypass the win32 wrapping branch, on any
    host platform -- covered separately from TestBuildDockerArgv since this
    exercises build_argv directly, not through build_docker_argv."""

    def test_container_true_never_wraps_in_powershell(self):
        argv = build_argv("agent", PROMPT, "/workspace", TIMEOUT_S, container=True)
        self.assertEqual(argv[0], "agent")
        self.assertNotIn(_WINDOWS_POWERSHELL, argv)

    def test_container_false_default_preserves_existing_behavior(self):
        """Default (container omitted) must be unaffected -- this is the
        exact call shape test_cursor_argv.py's existing tests already use."""
        argv = build_argv("C:\\tools\\cursor-agent.ps1", PROMPT, "C:\\repos\\myapp", TIMEOUT_S)
        import sys
        if sys.platform == "win32":
            self.assertEqual(argv[0], _WINDOWS_POWERSHELL)
        else:
            self.assertEqual(argv[0], "C:\\tools\\cursor-agent.ps1")


if __name__ == "__main__":
    unittest.main()
