import os
import sys
import tempfile
import unittest

from cadet.process.providers.copilot import build_argv

PROMPT = "rendered prompt text"
TIMEOUT_S = 1800

_BASE_TAIL = ["--output-format", "text", "--model", "auto", "--allow-all-tools", "--silent", "--no-color"]


@unittest.skipUnless(sys.platform == "win32", "node.exe/npm-loader.js resolution is Windows-specific")
class TestBuildArgvWindows(unittest.TestCase):
    """On Windows, build_argv bypasses both copilot.cmd and copilot.ps1 (each has
    its own unrelated bug — see copilot.py's module docstring) in favor of
    invoking node.exe + npm-loader.js directly. These tests fake up a minimal
    npm-global-style directory layout (copilot.cmd + node.exe + node_modules/
    @github/copilot/npm-loader.js) so build_argv's file-existence checks pass."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        basedir = self.tmp.name
        self.copilot_cmd = os.path.join(basedir, "copilot.cmd")
        self.node_exe = os.path.join(basedir, "node.exe")
        loader_dir = os.path.join(basedir, "node_modules", "@github", "copilot")
        os.makedirs(loader_dir, exist_ok=True)
        self.loader_path = os.path.join(loader_dir, "npm-loader.js")
        for p in (self.copilot_cmd, self.node_exe, self.loader_path):
            open(p, "w").close()
        self.cwd = os.path.join(basedir, "myapp")

    def test_minimal_argv_shape_and_order(self):
        argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S)
        self.assertEqual(argv, [self.node_exe, self.loader_path] + [
            "-p", PROMPT,
            "-C", self.cwd,
        ] + _BASE_TAIL + ["--mode", "plan", "--deny-tool", "shell"])

    def test_cwd_always_present_via_dash_c(self):
        for sandbox in (True, False):
            argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S, sandbox=sandbox)
            self.assertIn("-C", argv)
            self.assertEqual(argv[argv.index("-C") + 1], self.cwd)

    def test_allow_all_tools_always_present_regardless_of_flags(self):
        # Empirically confirmed required for non-interactive mode to complete
        # reliably at all — see copilot.py's module docstring.
        for skip_permissions in (True, False):
            for sandbox in (True, False):
                argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S, skip_permissions=skip_permissions, sandbox=sandbox)
                self.assertIn("--allow-all-tools", argv)

    def test_sandbox_true_uses_mode_plan_and_denies_shell(self):
        # --deny-tool shell is required alongside --mode plan: confirmed via a
        # real run that the model will otherwise use the shell tool to bypass
        # plan mode's edit restriction — see copilot.py's module docstring.
        argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S, sandbox=True, skip_permissions=False)
        idx = argv.index("--mode")
        self.assertEqual(argv[idx + 1], "plan")
        deny_idx = argv.index("--deny-tool")
        self.assertEqual(argv[deny_idx + 1], "shell")

    def test_skip_permissions_omits_mode_plan_and_deny_tool_regardless_of_sandbox(self):
        # Confirmed real edits both times: skip_permissions=True always wins
        # over sandbox, mirroring cursor's/codex's skip_permissions precedence.
        for sandbox in (True, False):
            argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S, skip_permissions=True, sandbox=sandbox)
            self.assertNotIn("--mode", argv)
            self.assertNotIn("--deny-tool", argv)

    def test_sandbox_false_without_skip_permissions_also_omits_mode_plan_and_deny_tool(self):
        # Confirmed platform-specific behavior (NOT a known-broken combo like
        # codex/cursor): --allow-all-tools is unconditional, so with no
        # --mode plan gate this behaves identically to skip_permissions=True.
        # See copilot.py's module docstring for the empirical confirmation.
        argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S, sandbox=False, skip_permissions=False)
        self.assertNotIn("--mode", argv)
        self.assertNotIn("--deny-tool", argv)

    def test_model_defaults_to_auto_when_unset(self):
        argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S, model=None)
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "auto")

    def test_model_appended_when_set(self):
        argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S, model="gpt-5.4")
        idx = argv.index("--model")
        self.assertEqual(argv[idx + 1], "gpt-5.4")

    def test_effort_appended_as_its_own_flag_when_set(self):
        # Unlike cursor's bracket-on-model-string syntax, copilot has a real
        # --effort flag — confirmed via a real invocation (see docstring).
        argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S, effort="high")
        idx = argv.index("--effort")
        self.assertEqual(argv[idx + 1], "high")

    def test_effort_omitted_when_unset(self):
        argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S, effort=None)
        self.assertNotIn("--effort", argv)

    def test_full_argv_with_all_options_set(self):
        argv = build_argv(
            self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S,
            model="claude-opus-4-8", effort="high",
            skip_permissions=True, sandbox=True,
        )
        self.assertEqual(argv, [self.node_exe, self.loader_path] + [
            "-p", PROMPT,
            "-C", self.cwd,
            "--output-format", "text",
            "--model", "claude-opus-4-8",
            "--allow-all-tools",
            "--silent",
            "--no-color",
            "--effort", "high",
        ])

    def test_prompt_and_cwd_never_shell_interpreted_stay_as_single_argv_entries(self):
        tricky_prompt = 'Do "this" <redirect> && rm -rf / ; echo done'
        argv = build_argv(self.copilot_cmd, tricky_prompt, self.cwd, TIMEOUT_S)
        self.assertEqual(argv[argv.index("-p") + 1], tricky_prompt)

    def test_invokes_node_exe_and_loader_not_the_cmd_or_ps1_shim(self):
        argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S)
        self.assertEqual(argv[0], self.node_exe)
        self.assertEqual(argv[1], self.loader_path)
        self.assertNotIn(self.copilot_cmd, argv)

    def test_missing_node_exe_and_node_path_env_raises(self):
        os.remove(self.node_exe)
        os.environ.pop("CADET_COPILOT_NODE_PATH", None)
        with self.assertRaises(RuntimeError):
            build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S)

    def test_missing_node_exe_falls_back_to_cadet_copilot_node_path_env(self):
        os.remove(self.node_exe)
        fallback_node = os.path.join(self.tmp.name, "alt_node.exe")
        open(fallback_node, "w").close()
        os.environ["CADET_COPILOT_NODE_PATH"] = fallback_node
        try:
            argv = build_argv(self.copilot_cmd, PROMPT, self.cwd, TIMEOUT_S)
            self.assertEqual(argv[0], fallback_node)
        finally:
            os.environ.pop("CADET_COPILOT_NODE_PATH", None)


if __name__ == "__main__":
    unittest.main()
