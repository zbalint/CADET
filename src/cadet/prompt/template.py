PROMPT_TEMPLATE = """# CADET Delegated Task
You are {agent_display_name}, running headless via CADET on behalf of Claude Code (the architect
agent for this project). There is no human available to answer follow-up questions in this
session — proceed autonomously using your best judgement and explicitly document any assumptions
you make in your final summary.

## Shared Memory Context
- Context ID: `{context_id}`
- Before starting, call search_memory(context_id="{context_id}", kwargs={}) (plus a keyword search
  on the task subject) to load prior findings/decisions logged under this thread by Claude Code or
  by earlier delegated jobs.
- Log meaningful milestones via log_event(context_id="{context_id}", agent_id="{agent_id}",
  kwargs={}).
- When you finish (success OR failure), you MUST:
  1. log_event(context_id="{context_id}", agent_id="{agent_id}", type="completion",
     content="<one-line summary + outcome>", kwargs={}).
  2. store_memory(context_id="{context_id}", owner_id="{agent_id}", ..., kwargs={}) for any
     durable finding/decision, so Claude Code can retrieve it later.

## CADET Job Metadata
- Job label: {label}
- Working directory: {cwd}
- CADET job id: {job_id}  (informational only — you have no CADET tools; do not attempt to call them)

## Your Task
{prompt}
"""


def render_prompt(context_id: str, label, cwd: str, job_id: str, prompt: str,
                   agent_id: str = "antigravity", display_name: str = "Antigravity (agy)") -> str:
    """Render the CADET delegated-task template via literal named-placeholder
    replacement rather than str.format(): the template body contains literal `{}`
    inside `kwargs={}` example calls, which would fight .format()'s brace-escaping
    rules. Placeholder names share no substrings, so sequential .replace() calls
    are unambiguous; `prompt` is substituted last so placeholder-like text inside
    the caller's own prompt is never mistaken for a template token. `agent_id`/
    `display_name` default to agy's identity for backward compatibility with
    callers that pre-date the multi-provider `provider` param.
    """
    rendered = PROMPT_TEMPLATE
    rendered = rendered.replace("{agent_display_name}", display_name)
    rendered = rendered.replace("{agent_id}", agent_id)
    rendered = rendered.replace("{context_id}", context_id)
    rendered = rendered.replace("{label}", label if label else "")
    rendered = rendered.replace("{cwd}", cwd)
    rendered = rendered.replace("{job_id}", job_id)
    rendered = rendered.replace("{prompt}", prompt)
    return rendered
