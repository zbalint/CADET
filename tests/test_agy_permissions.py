import json
import os
import shutil
import tempfile
import unittest

from cadet.process import agy_permissions


class AgyPermissionsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.settings_path = os.path.join(self.temp_dir, "settings.json")
        self.curated = ["command(git status)", "unsandboxed(git status)"]

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _read(self):
        with open(self.settings_path, "r", encoding="utf-8") as f:
            return json.load(f)


class TestLoadCuratedAllowlist(unittest.TestCase):
    def test_returns_nonempty_list_of_strings(self):
        curated = agy_permissions.load_curated_allowlist()
        self.assertGreater(len(curated), 0)
        self.assertTrue(all(isinstance(rule, str) for rule in curated))

    def test_returns_a_copy_not_the_shared_constant(self):
        curated = agy_permissions.load_curated_allowlist()
        curated.append("command(rm -rf /)")
        self.assertNotIn("command(rm -rf /)", agy_permissions.load_curated_allowlist())


class TestMergeAllowlist(AgyPermissionsTestCase):
    def test_creates_fresh_file_when_none_exists(self):
        self.assertFalse(os.path.isfile(self.settings_path))
        result = agy_permissions.merge_allowlist(self.settings_path, self.curated)
        self.assertEqual(result["added"], self.curated)
        self.assertEqual(result["already_present"], [])
        self.assertEqual(self._read()["permissions"]["allow"], self.curated)

    def test_adds_missing_curated_entries(self):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump({"permissions": {"allow": ["command(git status)"]}}, f)

        result = agy_permissions.merge_allowlist(self.settings_path, self.curated)
        self.assertEqual(result["added"], ["unsandboxed(git status)"])
        self.assertEqual(result["already_present"], ["command(git status)"])
        self.assertEqual(self._read()["permissions"]["allow"], self.curated)

    def test_additive_only_preserves_existing_user_entries_and_other_top_level_keys(self):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "model": "Gemini 3.6 Flash (Low)",
                    "trustedWorkspaces": ["C:\\Users\\zbalint\\Workspace\\SALTMDB"],
                    "permissions": {"allow": ["command(git add AGENT_GUIDE.md)"]},
                },
                f,
            )

        agy_permissions.merge_allowlist(self.settings_path, self.curated)
        settings = self._read()
        self.assertEqual(settings["model"], "Gemini 3.6 Flash (Low)")
        self.assertEqual(settings["trustedWorkspaces"], ["C:\\Users\\zbalint\\Workspace\\SALTMDB"])
        self.assertIn("command(git add AGENT_GUIDE.md)", settings["permissions"]["allow"])
        for rule in self.curated:
            self.assertIn(rule, settings["permissions"]["allow"])

    def test_idempotent_no_duplicates_on_rerun(self):
        agy_permissions.merge_allowlist(self.settings_path, self.curated)
        first_allow = self._read()["permissions"]["allow"]

        result = agy_permissions.merge_allowlist(self.settings_path, self.curated)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["already_present"], self.curated)
        self.assertEqual(self._read()["permissions"]["allow"], first_allow)

    def test_missing_permissions_key_is_created_if_absent(self):
        with open(self.settings_path, "w", encoding="utf-8") as f:
            json.dump({"model": "Gemini 3.6 Flash (Low)"}, f)

        agy_permissions.merge_allowlist(self.settings_path, self.curated)
        settings = self._read()
        self.assertEqual(settings["permissions"]["allow"], self.curated)
        self.assertEqual(settings["model"], "Gemini 3.6 Flash (Low)")


if __name__ == "__main__":
    unittest.main()
