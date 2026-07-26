import sys
import unittest

from cadet.process.providers.cursor import _WINDOWS_POWERSHELL, build_argv

CURSOR = "C:\\tools\\cursor-agent.ps1"
PROMPT = "rendered prompt text"
CWD = "C:\\repos\\myapp"
TIMEOUT_S = 1800

# On Windows, build_argv wraps cursor_path in a `powershell.exe -File` prefix
# to avoid cmd.exe's line reparsing corrupting prompts containing `<`/`>`
# (see cursor.py's module docstring) — these tests run on Windows, so the
# prefix is expected in every case.
_PREFIX = [_WINDOWS_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", CURSOR] if sys.platform == "win32" else [CURSOR]


class TestBuildArgv(unittest.TestCase):
    def test_minimal_argv_shape_and_order(self):
        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S)
        self.assertEqual(argv, _PREFIX + [
            "-p", PROMPT,
            "--workspace", CWD,
            "--output-format", "text",
            "--model", "auto",
            "--trust",
            "--mode", "plan",
        ])

    def test_cwd_always_present_via_workspace(self):
        for sandbox in (True, False):
            argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, sandbox=sandbox)
            self.assertIn("--workspace", argv)
            self.assertEqual(argv[argv.index("--workspace") + 1], CWD)

    def test_trust_always_present_regardless_of_flags(self):
        # Empirically confirmed required alongside --force for a directory
        # cursor-agent has never seen before — see cursor.py's module docstring.
        for skip_permissions in (True, False):
            for sandbox in (True, False):
                argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, skip_permissions=skip_permissions, sandbox=sandbox)
                self.assertIn("--trust", argv)

    def test_sandbox_true_uses_mode_plan(self):
        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, sandbox=True)
        idx = argv.index("--mode")
        self.assertEqual(argv[idx + 1], "plan")

    def test_sandbox_false_omits_mode_and_force(self):
        # NOTE: empirically confirmed non-functional for real edits on this
        # platform without skip_permissions=True (silent no-op, false success
        # claim) — still the documented/expected argv shape, see docstring.
        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, sandbox=False, skip_permissions=False)
        self.assertNotIn("--mode", argv)
        self.assertNotIn("--force", argv)

    def test_skip_permissions_uses_force_instead_of_mode_plan(self):
        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, skip_permissions=True)
        self.assertIn("--force", argv)
        self.assertNotIn("--mode", argv)

    def test_model_defaults_to_auto_when_unset(self):
        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, model=None)
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "auto")

    def test_model_appended_when_set(self):
        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, model="gpt-5.1")
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "gpt-5.1")

    def test_effort_only_applied_alongside_a_model_via_bracket_syntax(self):
        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, model=None, effort="high")
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "auto[effort=high]")

        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, model="claude-opus-4-8", effort="high")
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "claude-opus-4-8[effort=high]")

        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S, model="claude-opus-4-8", effort=None)
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "claude-opus-4-8")

    def test_full_argv_with_all_options_set(self):
        argv = build_argv(
            CURSOR, PROMPT, CWD, TIMEOUT_S,
            model="claude-opus-4-8", effort="high",
            skip_permissions=True, sandbox=True,
        )
        self.assertEqual(argv, _PREFIX + [
            "-p", PROMPT,
            "--workspace", CWD,
            "--output-format", "text",
            "--model", "claude-opus-4-8[effort=high]",
            "--trust",
            "--force",
        ])

    def test_prompt_and_cwd_never_shell_interpreted_stay_as_single_argv_entries(self):
        tricky_prompt = 'Do "this" <redirect> && rm -rf / ; echo done'
        argv = build_argv(CURSOR, tricky_prompt, CWD, TIMEOUT_S)
        self.assertEqual(argv[argv.index("-p") + 1], tricky_prompt)

    @unittest.skipUnless(sys.platform == "win32", "powershell.exe wrapping is Windows-only")
    def test_windows_wraps_cursor_path_via_powershell_file(self):
        argv = build_argv(CURSOR, PROMPT, CWD, TIMEOUT_S)
        self.assertEqual(argv[0], _WINDOWS_POWERSHELL)
        idx = argv.index("-File")
        self.assertEqual(argv[idx + 1], CURSOR)


if __name__ == "__main__":
    unittest.main()
