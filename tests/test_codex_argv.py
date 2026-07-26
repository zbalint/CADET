import unittest

from cadet.process.providers.codex import build_argv

CODEX = "C:\\tools\\codex.exe"
PROMPT = "rendered prompt text"
CWD = "C:\\repos\\myapp"
TIMEOUT_S = 1800


class TestBuildArgv(unittest.TestCase):
    def test_minimal_argv_shape_and_order(self):
        argv = build_argv(CODEX, PROMPT, CWD, TIMEOUT_S)
        self.assertEqual(argv, [
            CODEX, "exec", PROMPT,
            "-C", CWD,
            "--skip-git-repo-check",
            "--json",
            "-s", "read-only",
        ])

    def test_cwd_always_present_via_dash_c(self):
        for sandbox in (True, False):
            argv = build_argv(CODEX, PROMPT, CWD, TIMEOUT_S, sandbox=sandbox)
            self.assertIn("-C", argv)
            self.assertEqual(argv[argv.index("-C") + 1], CWD)

    def test_sandbox_true_uses_read_only(self):
        argv = build_argv(CODEX, PROMPT, CWD, TIMEOUT_S, sandbox=True)
        idx = argv.index("-s")
        self.assertEqual(argv[idx + 1], "read-only")

    def test_sandbox_false_uses_workspace_write(self):
        # NOTE: empirically confirmed broken on Windows (missing sandbox helper) —
        # still the correct flag mapping, just currently unreliable on this platform.
        argv = build_argv(CODEX, PROMPT, CWD, TIMEOUT_S, sandbox=False)
        idx = argv.index("-s")
        self.assertEqual(argv[idx + 1], "workspace-write")

    def test_skip_permissions_uses_bypass_flag_instead_of_dash_s(self):
        argv = build_argv(CODEX, PROMPT, CWD, TIMEOUT_S, skip_permissions=True)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertNotIn("-s", argv)

    def test_model_appended_only_when_set(self):
        argv = build_argv(CODEX, PROMPT, CWD, TIMEOUT_S, model=None)
        self.assertNotIn("-m", argv)
        argv = build_argv(CODEX, PROMPT, CWD, TIMEOUT_S, model="gpt-5.6-terra")
        idx = argv.index("-m")
        self.assertEqual(argv[idx + 1], "gpt-5.6-terra")

    def test_effort_appended_as_config_override_only_when_set(self):
        argv = build_argv(CODEX, PROMPT, CWD, TIMEOUT_S, effort=None)
        self.assertNotIn("-c", argv)
        argv = build_argv(CODEX, PROMPT, CWD, TIMEOUT_S, effort="low")
        idx = argv.index("-c")
        self.assertEqual(argv[idx + 1], "model_reasoning_effort=low")

    def test_full_argv_with_all_options_set(self):
        argv = build_argv(
            CODEX, PROMPT, CWD, TIMEOUT_S,
            model="gpt-5.6-terra", effort="high",
            skip_permissions=True, sandbox=True,
        )
        self.assertEqual(argv, [
            CODEX, "exec", PROMPT,
            "-C", CWD,
            "--skip-git-repo-check",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-m", "gpt-5.6-terra",
            "-c", "model_reasoning_effort=high",
        ])

    def test_prompt_and_cwd_never_shell_interpreted_stay_as_single_argv_entries(self):
        tricky_prompt = 'Do "this" && rm -rf / ; echo done'
        argv = build_argv(CODEX, tricky_prompt, CWD, TIMEOUT_S)
        self.assertEqual(argv[2], tricky_prompt)


if __name__ == "__main__":
    unittest.main()
