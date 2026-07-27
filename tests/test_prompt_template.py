import re
import unittest

from cadet.prompt.template import render_prompt

# The worked example from docs/PROMPT_PROTOCOL.md.
WORKED_EXAMPLE_ARGS = dict(
    context_id="task_refactor_auth_01",
    label="auth-refactor-step1",
    cwd="C:\\repos\\myapp",
    job_id="job-a1b2c3d4e5f6",
    prompt="Refactor the auth middleware to use the new token validator",
)

# Verbatim from the "Worked example" section of PROMPT_PROTOCOL.md. Its prose
# line-wrapping differs slightly from the raw template's (the doc's markdown
# source manually re-wraps paragraphs to ~100 cols, and "task_refactor_auth_01"
# is a different length than the "{context_id}" placeholder it replaces) — that
# wrapping is a markdown-source cosmetic artifact, not part of the literal
# contract, so comparisons below normalize whitespace rather than requiring an
# exact byte match.
EXPECTED_WORKED_EXAMPLE = """# CADET Delegated Task
You are Antigravity (agy), running headless via CADET on behalf of Claude Code (the architect
agent for this project). There is no human available to answer follow-up questions in this
session — proceed autonomously using your best judgement and explicitly document any assumptions
you make in your final summary.

## CADET Job Metadata
- Context ID: `task_refactor_auth_01`
- Agent ID: antigravity
- Job label: auth-refactor-step1
- Working directory: C:\\repos\\myapp
- CADET job id: job-a1b2c3d4e5f6  (informational only — you have no CADET or SALTMDB/MCP tools in
  this environment; do not attempt to call any, including search_memory/log_event/store_memory.
  Just write your complete answer as your final plain-text response.)

## Your Task
Refactor the auth middleware to use the new token validator
"""


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class TestRenderPrompt(unittest.TestCase):
    def test_worked_example_semantic_match(self):
        rendered = render_prompt(**WORKED_EXAMPLE_ARGS)
        self.assertEqual(_normalize_whitespace(rendered), _normalize_whitespace(EXPECTED_WORKED_EXAMPLE))

    def test_all_placeholders_substituted(self):
        rendered = render_prompt(**WORKED_EXAMPLE_ARGS)
        for placeholder in ("{context_id}", "{agent_id}", "{label}", "{cwd}", "{job_id}", "{prompt}"):
            self.assertNotIn(placeholder, rendered)

    def test_no_saltmdb_or_mcp_tool_call_instructions(self):
        # Regression guard: no delegated job of any provider (including the
        # containerized agy) has SALTMDB/MCP wired into its environment, so the
        # template must never instruct a job to call these tools — doing so
        # unconditionally previously caused cursor jobs to silently return empty
        # stdout on `status=succeeded`/`exit_code=0` (see render_prompt's docstring).
        rendered = render_prompt(**WORKED_EXAMPLE_ARGS)
        for forbidden in ("search_memory(", "log_event(", "store_memory("):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("you have no CADET or SALTMDB/MCP tools", rendered)

    def test_context_id_and_agent_id_appear_as_job_metadata(self):
        rendered = render_prompt(**WORKED_EXAMPLE_ARGS)
        self.assertIn('Context ID: `task_refactor_auth_01`', rendered)
        self.assertIn('Agent ID: antigravity', rendered)

    def test_prompt_text_placed_under_your_task_heading(self):
        rendered = render_prompt(**WORKED_EXAMPLE_ARGS)
        self.assertTrue(rendered.strip().endswith(WORKED_EXAMPLE_ARGS["prompt"]))

    def test_missing_label_renders_empty(self):
        args = dict(WORKED_EXAMPLE_ARGS)
        args["label"] = None
        rendered = render_prompt(**args)
        self.assertIn("- Job label: \n", rendered)

    def test_default_agent_identity_matches_agy_defaults(self):
        # Regression guard: the byte-identical output for agy's defaults must not
        # drift now that agent_id/display_name are parameterized rather than
        # hardcoded — this is the same assertion as test_worked_example_semantic_match,
        # just spelled out explicitly against the (default) agy identity.
        rendered = render_prompt(**WORKED_EXAMPLE_ARGS)
        self.assertIn("You are Antigravity (agy), running headless via CADET", rendered)
        self.assertIn("Agent ID: antigravity", rendered)

    def test_custom_agent_identity_is_substituted(self):
        args = dict(WORKED_EXAMPLE_ARGS)
        rendered = render_prompt(**args, agent_id="codex", display_name="OpenAI Codex CLI")
        self.assertIn("You are OpenAI Codex CLI, running headless via CADET", rendered)
        self.assertIn("Agent ID: codex", rendered)
        self.assertNotIn("antigravity", rendered)

    def test_prompt_containing_placeholder_like_text_is_not_double_substituted(self):
        args = dict(WORKED_EXAMPLE_ARGS)
        args["prompt"] = "Reference the working directory as {cwd} in your summary."
        rendered = render_prompt(**args)
        # The template's own {cwd} slot must resolve to the real cwd...
        self.assertIn("Working directory: C:\\repos\\myapp", rendered)
        # ...while the caller-supplied literal text "{cwd}" inside their prompt
        # must survive untouched (not further substituted).
        self.assertIn("Reference the working directory as {cwd} in your summary.", rendered)


if __name__ == "__main__":
    unittest.main()
