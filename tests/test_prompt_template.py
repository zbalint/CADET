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

## Shared Memory Context
- Context ID: `task_refactor_auth_01`
- Before starting, call search_memory(context_id="task_refactor_auth_01", kwargs={}) (plus a
  keyword search on the task subject) to load prior findings/decisions logged under this thread by
  Claude Code or by earlier delegated jobs.
- Log meaningful milestones via log_event(context_id="task_refactor_auth_01",
  agent_id="antigravity", kwargs={}).
- When you finish (success OR failure), you MUST:
  1. log_event(context_id="task_refactor_auth_01", agent_id="antigravity", type="completion",
     content="<one-line summary + outcome>", kwargs={}).
  2. store_memory(context_id="task_refactor_auth_01", owner_id="antigravity", ..., kwargs={}) for
     any durable finding/decision, so Claude Code can retrieve it later.

## CADET Job Metadata
- Job label: auth-refactor-step1
- Working directory: C:\\repos\\myapp
- CADET job id: job-a1b2c3d4e5f6  (informational only — you have no CADET tools; do not attempt to
  call them)

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
        for placeholder in ("{context_id}", "{label}", "{cwd}", "{job_id}", "{prompt}"):
            self.assertNotIn(placeholder, rendered)

    def test_literal_kwargs_braces_untouched(self):
        rendered = render_prompt(**WORKED_EXAMPLE_ARGS)
        self.assertIn("kwargs={}", rendered)

    def test_context_id_appears_in_shared_memory_section(self):
        rendered = render_prompt(**WORKED_EXAMPLE_ARGS)
        self.assertIn('Context ID: `task_refactor_auth_01`', rendered)
        self.assertIn('search_memory(context_id="task_refactor_auth_01"', rendered)

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
        self.assertIn('agent_id="antigravity"', rendered)
        self.assertIn('owner_id="antigravity"', rendered)

    def test_custom_agent_identity_is_substituted(self):
        args = dict(WORKED_EXAMPLE_ARGS)
        rendered = render_prompt(**args, agent_id="codex", display_name="OpenAI Codex CLI")
        self.assertIn("You are OpenAI Codex CLI, running headless via CADET", rendered)
        self.assertIn('agent_id="codex"', rendered)
        self.assertIn('owner_id="codex"', rendered)
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
