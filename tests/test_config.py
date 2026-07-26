import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cadet import config

_ENV_VARS = [
    "CADET_STATE_DIR",
    "CADET_DEFAULT_CWD",
    "CADET_MAX_CONCURRENT",
    "CADET_DEFAULT_TIMEOUT_S",
    "CADET_MAX_TIMEOUT_S",
    "CADET_LOG_RETENTION_DAYS",
    "CADET_AGY_MODEL",
    "CADET_AGY_EFFORT",
    "CADET_AGY_SANDBOX",
    "CADET_AGY_SETTINGS_PATH",
    "CADET_AGY_DOCKER_IMAGE",
    "CADET_AGY_GEMINI_VOLUME",
    "CADET_AGY_CONTAINER_MEMORY",
    "CADET_AGY_CONTAINER_CPUS",
    "CADET_AGY_CONTAINER_PIDS_LIMIT",
    "CADET_AGY_STOP_GRACE_S",
    "CADET_CODEX_PATH",
    "CADET_CODEX_MODEL",
    "CADET_CODEX_EFFORT",
    "CADET_CODEX_SANDBOX",
    "CADET_CODEX_DOCKER_IMAGE",
    "CADET_CODEX_AUTH_VOLUME",
    "CADET_CODEX_CONTAINER_MEMORY",
    "CADET_CODEX_CONTAINER_CPUS",
    "CADET_CODEX_CONTAINER_PIDS_LIMIT",
    "CADET_CODEX_STOP_GRACE_S",
    "CADET_CURSOR_PATH",
    "CADET_WEB_HOST",
    "CADET_WEB_PORT",
    "CADET_WEB_ENABLED",
]


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._saved_env = {var: os.environ.get(var) for var in _ENV_VARS}
        for var in _ENV_VARS:
            os.environ.pop(var, None)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        for var, val in self._saved_env.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class TestStateDirDefaults(ConfigTestCase):
    def test_state_dir_defaults_to_home(self):
        state_dir = config.get_state_dir()
        self.assertEqual(state_dir, os.path.expanduser("~/.cadet"))

    def test_state_dir_override(self):
        override = os.path.join(self.temp_dir, "custom_state")
        os.environ["CADET_STATE_DIR"] = override
        self.assertEqual(config.get_state_dir(), override)
        self.assertTrue(os.path.isdir(override))

    def test_db_path_under_state_dir(self):
        os.environ["CADET_STATE_DIR"] = self.temp_dir
        db_path = config.get_db_path()
        self.assertEqual(db_path, os.path.join(self.temp_dir, "state", "cadet.db"))
        self.assertTrue(os.path.isdir(os.path.dirname(db_path)))

    def test_logs_dir_and_job_log_dir(self):
        os.environ["CADET_STATE_DIR"] = self.temp_dir
        logs_dir = config.get_logs_dir()
        self.assertEqual(logs_dir, os.path.join(self.temp_dir, "logs"))
        job_dir = config.get_job_log_dir("job-abc123")
        self.assertEqual(job_dir, os.path.join(logs_dir, "job-abc123"))
        self.assertTrue(os.path.isdir(job_dir))


class TestNumericBoolGetters(ConfigTestCase):
    def test_defaults(self):
        self.assertIsNone(config.get_default_cwd())
        self.assertEqual(config.get_max_concurrent(), 2)
        self.assertEqual(config.get_default_timeout_s(), 1800)
        self.assertEqual(config.get_max_timeout_s(), 7200)
        self.assertEqual(config.get_log_retention_days(), 14)
        self.assertIsNone(config.get_agy_model())
        self.assertIsNone(config.get_agy_effort())
        self.assertTrue(config.is_agy_sandbox_enabled())

    def test_overrides(self):
        os.environ["CADET_DEFAULT_CWD"] = self.temp_dir
        os.environ["CADET_MAX_CONCURRENT"] = "5"
        os.environ["CADET_DEFAULT_TIMEOUT_S"] = "60"
        os.environ["CADET_MAX_TIMEOUT_S"] = "120"
        os.environ["CADET_LOG_RETENTION_DAYS"] = "1"
        os.environ["CADET_AGY_MODEL"] = "gemini-3.6-flash-medium"
        os.environ["CADET_AGY_EFFORT"] = "high"

        self.assertEqual(config.get_default_cwd(), self.temp_dir)
        self.assertEqual(config.get_max_concurrent(), 5)
        self.assertEqual(config.get_default_timeout_s(), 60)
        self.assertEqual(config.get_max_timeout_s(), 120)
        self.assertEqual(config.get_log_retention_days(), 1)
        self.assertEqual(config.get_agy_model(), "gemini-3.6-flash-medium")
        self.assertEqual(config.get_agy_effort(), "high")

    def test_sandbox_flag_falsy_values(self):
        for val in ("0", "false", "False", "no", "off"):
            os.environ["CADET_AGY_SANDBOX"] = val
            self.assertFalse(config.is_agy_sandbox_enabled(), msg=f"expected falsy for {val!r}")

    def test_sandbox_flag_truthy_values(self):
        for val in ("1", "true", "True", "yes", "on"):
            os.environ["CADET_AGY_SANDBOX"] = val
            self.assertTrue(config.is_agy_sandbox_enabled(), msg=f"expected truthy for {val!r}")


class TestAgyDockerGetters(ConfigTestCase):
    def test_defaults(self):
        self.assertEqual(config.get_agy_docker_image(), "cadet-agy:latest")
        self.assertEqual(config.get_agy_gemini_volume(), "cadet-agy-gemini")
        self.assertEqual(config.get_agy_container_memory(), "2g")
        self.assertEqual(config.get_agy_container_cpus(), "2")
        self.assertEqual(config.get_agy_container_pids_limit(), 512)
        self.assertEqual(config.get_agy_stop_grace_s(), 10)

    def test_overrides(self):
        os.environ["CADET_AGY_DOCKER_IMAGE"] = "custom-agy:v2"
        os.environ["CADET_AGY_GEMINI_VOLUME"] = "custom-vol"
        os.environ["CADET_AGY_CONTAINER_MEMORY"] = "4g"
        os.environ["CADET_AGY_CONTAINER_CPUS"] = "4"
        os.environ["CADET_AGY_CONTAINER_PIDS_LIMIT"] = "1024"
        os.environ["CADET_AGY_STOP_GRACE_S"] = "30"

        self.assertEqual(config.get_agy_docker_image(), "custom-agy:v2")
        self.assertEqual(config.get_agy_gemini_volume(), "custom-vol")
        self.assertEqual(config.get_agy_container_memory(), "4g")
        self.assertEqual(config.get_agy_container_cpus(), "4")
        self.assertEqual(config.get_agy_container_pids_limit(), 1024)
        self.assertEqual(config.get_agy_stop_grace_s(), 30)


class TestResolveAgyDockerImage(ConfigTestCase):
    def test_image_found_resolves(self):
        with patch("cadet.config.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as mock_run:
            self.assertEqual(config.resolve_agy_docker_image(), "cadet-agy:latest")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0], ["docker", "image", "inspect", "cadet-agy:latest"])

    def test_image_not_found_raises(self):
        with patch("cadet.config.subprocess.run", return_value=subprocess.CompletedProcess([], 1, stderr="no such image")):
            with self.assertRaises(RuntimeError):
                config.resolve_agy_docker_image()

    def test_docker_cli_missing_raises(self):
        with patch("cadet.config.subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(RuntimeError):
                config.resolve_agy_docker_image()

    def test_docker_daemon_unreachable_raises(self):
        with patch("cadet.config.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=10)):
            with self.assertRaises(RuntimeError):
                config.resolve_agy_docker_image()

    def test_respects_image_env_override(self):
        os.environ["CADET_AGY_DOCKER_IMAGE"] = "custom-agy:v2"
        with patch("cadet.config.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as mock_run:
            self.assertEqual(config.resolve_agy_docker_image(), "custom-agy:v2")
        self.assertEqual(mock_run.call_args[0][0], ["docker", "image", "inspect", "custom-agy:v2"])


class TestCodexDockerGetters(ConfigTestCase):
    def test_defaults(self):
        self.assertEqual(config.get_codex_docker_image(), "cadet-codex:latest")
        self.assertEqual(config.get_codex_auth_volume(), "cadet-codex-auth")
        self.assertEqual(config.get_codex_container_memory(), "2g")
        self.assertEqual(config.get_codex_container_cpus(), "2")
        self.assertEqual(config.get_codex_container_pids_limit(), 512)
        self.assertEqual(config.get_codex_stop_grace_s(), 10)

    def test_overrides(self):
        os.environ["CADET_CODEX_DOCKER_IMAGE"] = "custom-codex:v2"
        os.environ["CADET_CODEX_AUTH_VOLUME"] = "custom-vol"
        os.environ["CADET_CODEX_CONTAINER_MEMORY"] = "4g"
        os.environ["CADET_CODEX_CONTAINER_CPUS"] = "4"
        os.environ["CADET_CODEX_CONTAINER_PIDS_LIMIT"] = "1024"
        os.environ["CADET_CODEX_STOP_GRACE_S"] = "30"

        self.assertEqual(config.get_codex_docker_image(), "custom-codex:v2")
        self.assertEqual(config.get_codex_auth_volume(), "custom-vol")
        self.assertEqual(config.get_codex_container_memory(), "4g")
        self.assertEqual(config.get_codex_container_cpus(), "4")
        self.assertEqual(config.get_codex_container_pids_limit(), 1024)
        self.assertEqual(config.get_codex_stop_grace_s(), 30)


class TestResolveCodexDockerImage(ConfigTestCase):
    def test_image_found_resolves(self):
        with patch("cadet.config.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as mock_run:
            self.assertEqual(config.resolve_codex_docker_image(), "cadet-codex:latest")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0], ["docker", "image", "inspect", "cadet-codex:latest"])

    def test_image_not_found_raises(self):
        with patch("cadet.config.subprocess.run", return_value=subprocess.CompletedProcess([], 1, stderr="no such image")):
            with self.assertRaises(RuntimeError):
                config.resolve_codex_docker_image()

    def test_docker_cli_missing_raises(self):
        with patch("cadet.config.subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(RuntimeError):
                config.resolve_codex_docker_image()

    def test_docker_daemon_unreachable_raises(self):
        with patch("cadet.config.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=10)):
            with self.assertRaises(RuntimeError):
                config.resolve_codex_docker_image()

    def test_respects_image_env_override(self):
        os.environ["CADET_CODEX_DOCKER_IMAGE"] = "custom-codex:v2"
        with patch("cadet.config.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as mock_run:
            self.assertEqual(config.resolve_codex_docker_image(), "custom-codex:v2")
        self.assertEqual(mock_run.call_args[0][0], ["docker", "image", "inspect", "custom-codex:v2"])


class TestAgySettingsPath(ConfigTestCase):
    def test_defaults_to_gemini_antigravity_cli_settings(self):
        self.assertEqual(
            config.get_agy_settings_path(),
            os.path.expanduser("~/.gemini/antigravity-cli/settings.json"),
        )

    def test_override(self):
        override = os.path.join(self.temp_dir, "custom_settings.json")
        os.environ["CADET_AGY_SETTINGS_PATH"] = override
        self.assertEqual(config.get_agy_settings_path(), override)


class TestProviderGenericResolution(ConfigTestCase):
    def test_resolve_provider_path_agy_delegates_to_resolve_agy_docker_image(self):
        with patch("cadet.config.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(config.resolve_provider_path("agy"), "cadet-agy:latest")

    def test_resolve_provider_path_codex_delegates_to_resolve_codex_docker_image(self):
        with patch("cadet.config.subprocess.run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(config.resolve_provider_path("codex"), "cadet-codex:latest")

    def test_resolve_provider_path_other_provider_uses_prefixed_env_var(self):
        fake_cursor = os.path.join(self.temp_dir, "cursor-agent.ps1")
        with open(fake_cursor, "w") as f:
            f.write("")
        os.environ["CADET_CURSOR_PATH"] = fake_cursor
        self.assertEqual(config.resolve_provider_path("cursor"), fake_cursor)

    def test_resolve_provider_path_missing_raises(self):
        with self.assertRaises(RuntimeError):
            config.resolve_provider_path("cursor")

    def test_resolve_provider_path_nonexistent_file_raises(self):
        os.environ["CADET_CURSOR_PATH"] = os.path.join(self.temp_dir, "does_not_exist.ps1")
        with self.assertRaises(RuntimeError):
            config.resolve_provider_path("cursor")

    def test_get_provider_model_effort_sandbox_agy_matches_legacy_getters(self):
        os.environ["CADET_AGY_MODEL"] = "gemini-3.6-flash-medium"
        os.environ["CADET_AGY_EFFORT"] = "high"
        self.assertEqual(config.get_provider_model("agy"), config.get_agy_model())
        self.assertEqual(config.get_provider_effort("agy"), config.get_agy_effort())
        self.assertEqual(config.is_provider_sandbox_enabled("agy"), config.is_agy_sandbox_enabled())

    def test_get_provider_model_effort_sandbox_other_provider_uses_own_env_vars(self):
        os.environ["CADET_CODEX_MODEL"] = "gpt-5.2"
        os.environ["CADET_CODEX_EFFORT"] = "low"
        os.environ["CADET_CODEX_SANDBOX"] = "false"
        self.assertEqual(config.get_provider_model("codex"), "gpt-5.2")
        self.assertEqual(config.get_provider_effort("codex"), "low")
        self.assertFalse(config.is_provider_sandbox_enabled("codex"))

    def test_get_provider_model_effort_default_none(self):
        self.assertIsNone(config.get_provider_model("codex"))
        self.assertIsNone(config.get_provider_effort("codex"))

    def test_provider_sandbox_defaults_true(self):
        self.assertTrue(config.is_provider_sandbox_enabled("codex"))


class TestWebConfig(ConfigTestCase):
    def test_defaults(self):
        self.assertEqual(config.get_web_host(), "127.0.0.1")
        self.assertEqual(config.get_web_port(), 8420)
        self.assertTrue(config.is_web_enabled())

    def test_overrides(self):
        os.environ["CADET_WEB_HOST"] = "0.0.0.0"
        os.environ["CADET_WEB_PORT"] = "9000"
        self.assertEqual(config.get_web_host(), "0.0.0.0")
        self.assertEqual(config.get_web_port(), 9000)

    def test_enabled_flag_falsy_values(self):
        for val in ("0", "false", "False", "no", "off"):
            os.environ["CADET_WEB_ENABLED"] = val
            self.assertFalse(config.is_web_enabled(), msg=f"expected falsy for {val!r}")

    def test_enabled_flag_truthy_values(self):
        for val in ("1", "true", "True", "yes", "on"):
            os.environ["CADET_WEB_ENABLED"] = val
            self.assertTrue(config.is_web_enabled(), msg=f"expected truthy for {val!r}")


class TestClampTimeout(ConfigTestCase):
    def test_none_uses_default(self):
        os.environ["CADET_DEFAULT_TIMEOUT_S"] = "300"
        self.assertEqual(config.clamp_timeout_s(None), 300)

    def test_value_under_max_passes_through(self):
        os.environ["CADET_MAX_TIMEOUT_S"] = "7200"
        self.assertEqual(config.clamp_timeout_s(500), 500)

    def test_value_over_max_is_clamped(self):
        os.environ["CADET_MAX_TIMEOUT_S"] = "100"
        self.assertEqual(config.clamp_timeout_s(9999), 100)


if __name__ == "__main__":
    unittest.main()
