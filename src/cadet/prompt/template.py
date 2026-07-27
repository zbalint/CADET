PROMPT_TEMPLATE = """# CADET Delegated Task
You are {agent_display_name}, running headless via CADET on behalf of Claude Code (the architect
agent for this project). There is no human available to answer follow-up questions in this
session — proceed autonomously using your best judgement and explicitly document any assumptions
you make in your final summary.

## CADET Job Metadata
- Context ID: `{context_id}`
- Agent ID: {agent_id}
- Job label: {label}
- Working directory: {cwd}
- CADET job id: {job_id}  (informational only — you have no CADET or SALTMDB/MCP tools in this
  environment; do not attempt to call any, including search_memory/log_event/store_memory. Just
  write your complete answer as your final plain-text response.)

## Your Task
{prompt}
"""


def render_prompt(context_id: str, label, cwd: str, job_id: str, prompt: str,
                   agent_id: str = "antigravity", display_name: str = "Antigravity (agy)") -> str:
    """Render the CADET delegated-task template via literal named-placeholder
    replacement rather than str.format(): the caller-supplied `prompt` text can
    itself contain literal brace placeholders (e.g. a prompt that says "reference
    the working directory as {cwd}"), which would fight .format()'s brace-escaping
    rules. Placeholder names share no substrings, so sequential .replace() calls
    are unambiguous; `prompt` is substituted last so placeholder-like text inside
    the caller's own prompt is never mistaken for a template token. `agent_id`/
    `display_name` default to agy's identity for backward compatibility with
    callers that pre-date the multi-provider `provider` param.

    No delegated job of any provider (including the containerized `agy`) has SALTMDB/MCP wired
    into its environment — only the user's own host-installed, interactive `agy` does. The template
    therefore never instructs delegated jobs to call search_memory/log_event/store_memory: doing so
    unconditionally for every provider caused a confirmed failure mode where `cursor` in particular
    would attempt the (nonexistent) mandated tool call as its final turn and never fall back to
    producing user-facing text, silently exiting `status=succeeded`/`exit_code=0` with empty stdout
    even on real, substantive tasks (reproduced 2026-07-27, survived even an explicit per-call
    instruction telling it to ignore the requirement). `context_id`/`agent_id` are kept in the
    rendered prompt as informational job metadata only (useful for a human reading logs), not as an
    instruction to call anything.
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
