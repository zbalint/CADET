import os
import shutil
import tempfile
import unittest

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
    "CADET_AGY_PATH",
    "CADET_AGY_SETTINGS_PATH",
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


class TestResolveAgyPath(ConfigTestCase):
    def test_missing_raises(self):
        with self.assertRaises(RuntimeError):
            config.resolve_agy_path()

    def test_nonexistent_path_raises(self):
        os.environ["CADET_AGY_PATH"] = os.path.join(self.temp_dir, "does_not_exist.exe")
        with self.assertRaises(RuntimeError):
            config.resolve_agy_path()

    def test_valid_path_resolves(self):
        fake_agy = os.path.join(self.temp_dir, "agy.exe")
        with open(fake_agy, "w") as f:
            f.write("")
        os.environ["CADET_AGY_PATH"] = fake_agy
        self.assertEqual(config.resolve_agy_path(), fake_agy)


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
