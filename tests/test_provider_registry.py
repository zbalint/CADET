import unittest

from cadet.process.providers import agy, codex, copilot, cursor, registry


class TestProviderRegistry(unittest.TestCase):
    def test_get_agy_returns_agy_module(self):
        self.assertIs(registry.get("agy"), agy)

    def test_get_codex_returns_codex_module(self):
        self.assertIs(registry.get("codex"), codex)

    def test_get_cursor_returns_cursor_module(self):
        self.assertIs(registry.get("cursor"), cursor)

    def test_get_copilot_returns_copilot_module(self):
        self.assertIs(registry.get("copilot"), copilot)

    def test_get_none_defaults_to_default_provider(self):
        self.assertIs(registry.get(None), agy)
        self.assertEqual(registry.DEFAULT_PROVIDER, "agy")

    def test_get_unknown_raises(self):
        with self.assertRaises(ValueError):
            registry.get("bogus")

    def test_names_contains_agy_and_codex_and_cursor_and_copilot(self):
        self.assertIn("agy", registry.names())
        self.assertIn("codex", registry.names())
        self.assertIn("cursor", registry.names())
        self.assertIn("copilot", registry.names())

    def test_agy_module_exposes_required_attrs(self):
        for attr in ("NAME", "AGENT_ID", "DISPLAY_NAME", "build_argv", "spawn", "parse_error"):
            self.assertTrue(hasattr(agy, attr), msg=f"agy module missing {attr}")
        self.assertEqual(agy.NAME, "agy")

    def test_codex_module_exposes_required_attrs(self):
        for attr in ("NAME", "AGENT_ID", "DISPLAY_NAME", "build_argv", "spawn", "parse_error"):
            self.assertTrue(hasattr(codex, attr), msg=f"codex module missing {attr}")
        self.assertEqual(codex.NAME, "codex")

    def test_cursor_module_exposes_required_attrs(self):
        for attr in ("NAME", "AGENT_ID", "DISPLAY_NAME", "build_argv", "spawn", "parse_error"):
            self.assertTrue(hasattr(cursor, attr), msg=f"cursor module missing {attr}")
        self.assertEqual(cursor.NAME, "cursor")

    def test_copilot_module_exposes_required_attrs(self):
        for attr in ("NAME", "AGENT_ID", "DISPLAY_NAME", "build_argv", "spawn", "parse_error"):
            self.assertTrue(hasattr(copilot, attr), msg=f"copilot module missing {attr}")
        self.assertEqual(copilot.NAME, "copilot")


if __name__ == "__main__":
    unittest.main()
