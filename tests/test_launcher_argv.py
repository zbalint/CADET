import unittest

from cadet.process.launcher import _inner_agy_argv

PROMPT = "rendered prompt text"
TIMEOUT_S = 1800


class TestInnerAgyArgv(unittest.TestCase):
    """Tests the pure agy-flag logic run inside the container. --add-dir
    always targets the fixed container-side "/workspace" path now (never a
    host cwd) -- see launcher.build_argv for the outer `docker run` wrapping,
    covered separately in test_agy_docker_argv.py."""

    def test_minimal_argv_shape_and_order(self):
        argv = _inner_agy_argv(PROMPT, TIMEOUT_S)
        self.assertEqual(argv, [
            "agy", "-p", PROMPT,
            "--add-dir", "/workspace",
            "--print-timeout", "1800s",
            "--mode", "accept-edits",
            "--sandbox",
        ])

    def test_add_dir_always_present_and_targets_workspace(self):
        for sandbox in (True, False):
            argv = _inner_agy_argv(PROMPT, TIMEOUT_S, sandbox=sandbox)
            self.assertIn("--add-dir", argv)
            self.assertEqual(argv[argv.index("--add-dir") + 1], "/workspace")

    def test_print_timeout_formatted_with_seconds_suffix(self):
        argv = _inner_agy_argv(PROMPT, 42)
        idx = argv.index("--print-timeout")
        self.assertEqual(argv[idx + 1], "42s")

    def test_sandbox_flag_present_by_default(self):
        argv = _inner_agy_argv(PROMPT, TIMEOUT_S)
        self.assertIn("--sandbox", argv)

    def test_sandbox_flag_absent_when_disabled(self):
        argv = _inner_agy_argv(PROMPT, TIMEOUT_S, sandbox=False)
        self.assertNotIn("--sandbox", argv)

    def test_model_appended_only_when_set(self):
        argv = _inner_agy_argv(PROMPT, TIMEOUT_S, model=None)
        self.assertNotIn("--model", argv)
        argv = _inner_agy_argv(PROMPT, TIMEOUT_S, model="gemini-3.6-flash-high")
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "gemini-3.6-flash-high")

    def test_effort_appended_only_when_set(self):
        argv = _inner_agy_argv(PROMPT, TIMEOUT_S, effort=None)
        self.assertNotIn("--effort", argv)
        argv = _inner_agy_argv(PROMPT, TIMEOUT_S, effort="high")
        idx = argv.index("--effort")
        self.assertEqual(argv[idx + 1], "high")

    def test_skip_permissions_appended_only_when_true(self):
        argv = _inner_agy_argv(PROMPT, TIMEOUT_S, skip_permissions=False)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        argv = _inner_agy_argv(PROMPT, TIMEOUT_S, skip_permissions=True)
        self.assertIn("--dangerously-skip-permissions", argv)

    def test_full_argv_with_all_options_set(self):
        argv = _inner_agy_argv(
            PROMPT, TIMEOUT_S,
            model="gemini-3.6-flash-high", effort="high",
            skip_permissions=True, sandbox=True,
        )
        self.assertEqual(argv, [
            "agy", "-p", PROMPT,
            "--add-dir", "/workspace",
            "--print-timeout", "1800s",
            "--mode", "accept-edits",
            "--sandbox",
            "--model", "gemini-3.6-flash-high",
            "--effort", "high",
            "--dangerously-skip-permissions",
        ])

    def test_prompt_never_shell_interpreted_stays_as_single_argv_entry(self):
        tricky_prompt = 'Do "this" && rm -rf / ; echo done'
        argv = _inner_agy_argv(tricky_prompt, TIMEOUT_S)
        self.assertEqual(argv[2], tricky_prompt)


if __name__ == "__main__":
    unittest.main()
