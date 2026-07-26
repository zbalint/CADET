# CADET Prompt Protocol

> Status: design-only — see [ARCHITECTURE.md](./ARCHITECTURE.md) for the actors involved and
> [MCP_TOOLS.md](./MCP_TOOLS.md) for `delegate_task`'s `context_id`/`label`/`cwd` params referenced
> below.

## Why CADET wraps the prompt at all

Antigravity's own global instructions (`~/.gemini/GEMINI.md`) already fully specify how it should
use SALTMDB: bootstrap search on `#core` tags and the active task, `context_id` tagging on
everything it stores/logs, and wrap-up logging before finishing. CADET does not need to re-teach
`agy` how to use SALTMDB — it only needs to:

1. Hand `agy` the specific `context_id` for this task, so its existing instructions have
   something concrete to search/tag against.
2. Flag that this is a **headless, unattended** invocation — a concern specific to the CADET
   delegation path, not something `GEMINI.md` already covers, since normally `agy` runs
   interactively with a human available to answer follow-ups.

Everything else in the template is just enough job metadata for `agy` to orient itself; it should
never attempt to call CADET's own MCP tools (it has no `delegate_task`/`check_task_status`/etc. —
those exist only on Claude Code's side of the connection).

## Template

`delegate_task`'s `prompt` param is substituted into this template before being (a) written to
`logs/<job_id>/prompt.txt` as an audit trail, and (b) passed verbatim as the `-p` argument to
`agy`:

```
# CADET Delegated Task
You are Antigravity (agy), running headless via CADET on behalf of Claude Code (the architect
agent for this project). There is no human available to answer follow-up questions in this
session — proceed autonomously using your best judgement and explicitly document any assumptions
you make in your final summary.

## Shared Memory Context
- Context ID: `{context_id}`
- Before starting, call search_memory(context_id="{context_id}", kwargs={}) (plus a keyword search
  on the task subject) to load prior findings/decisions logged under this thread by Claude Code or
  by earlier delegated jobs.
- Log meaningful milestones via log_event(context_id="{context_id}", agent_id="antigravity",
  kwargs={}).
- When you finish (success OR failure), you MUST:
  1. log_event(context_id="{context_id}", agent_id="antigravity", type="completion",
     content="<one-line summary + outcome>", kwargs={}).
  2. store_memory(context_id="{context_id}", owner_id="antigravity", ..., kwargs={}) for any
     durable finding/decision, so Claude Code can retrieve it later.

## CADET Job Metadata
- Job label: {label}
- Working directory: {cwd}
- CADET job id: {job_id}  (informational only — you have no CADET tools; do not attempt to call them)

## Your Task
{prompt}
```

## `context_id` conventions

- `context_id` is intentionally opaque/free-text — SALTMDB itself doesn't validate its format
  (see the global `CLAUDE.md`/`GEMINI.md` example convention, e.g. `task_refactor_auth_01`).
- If the caller doesn't supply one, `delegate_task` generates `"cadet-<uuid4hex[:8]>"` and returns
  it; the caller should treat the returned value as authoritative for any follow-up jobs on the
  same thread.
- **One `context_id` may span many `delegate_task`/`job_id` calls.** Each `agy` process is a cold
  session with no memory of earlier jobs — the mandated SALTMDB search at the top of the template
  is what actually threads continuity across jobs, not CADET itself. CADET's only role is making
  sure every job under the same logical task gets the same `context_id` baked into its prompt.

## Worked example

Given `delegate_task(prompt="Refactor the auth middleware to use the new token validator",
context_id="task_refactor_auth_01", cwd="C:\\repos\\myapp", label="auth-refactor-step1")`, the
rendered prompt written to `logs/job-a1b2c3d4e5f6/prompt.txt` and passed to `agy -p` is:

```
# CADET Delegated Task
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
- Working directory: C:\repos\myapp
- CADET job id: job-a1b2c3d4e5f6  (informational only — you have no CADET tools; do not attempt to
  call them)

## Your Task
Refactor the auth middleware to use the new token validator
```
