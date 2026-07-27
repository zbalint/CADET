# CADET Prompt Protocol

> Status: design-only — see [ARCHITECTURE.md](./ARCHITECTURE.md) for the actors involved and
> [MCP_TOOLS.md](./MCP_TOOLS.md) for `delegate_task`'s `context_id`/`label`/`cwd` params referenced
> below.

## Why CADET wraps the prompt at all

The template's job is narrow: tell the delegated CLI (whichever provider) that it's running
**headless and unattended** — a concern specific to the CADET delegation path, since normally each
of these CLIs runs interactively with a human available to answer follow-ups — and hand it enough
job metadata (`context_id`/`agent_id`/`label`/`cwd`/`job_id`) to orient itself and to let a human
correlate its output with CADET's own job store/logs later.

**No delegated job has SALTMDB or any other MCP tool wired into its environment — not even the
containerized `agy` CADET dispatches jobs to.** Only the user's own separate, host-installed,
interactive `agy` session has SALTMDB access; that instance's own global instructions
(`~/.gemini/GEMINI.md`) already fully specify how it uses SALTMDB, but those instructions do not
carry over to a headless job spawned by CADET, because CADET's `agy`/`codex`/`cursor`/`copilot`
jobs all run inside a Docker container built from a fresh image, not the user's own configured
environment. The template previously instructed every delegated job to call
`search_memory`/`log_event`/`store_memory` unconditionally, which caused a confirmed failure mode:
`cursor` in particular would attempt the (nonexistent) mandated tool call as its final turn and
never fall back to producing user-facing text, silently returning `status=succeeded`/`exit_code=0`
with **empty stdout** even on real, substantive tasks (reproduced 2026-07-27) — and this survived
even an explicit per-call instruction in the caller's own prompt telling it to ignore the
requirement, so the fix had to live in the template itself. The template now never instructs a
delegated job to call any CADET or SALTMDB/MCP tool; it explicitly tells the job those tools don't
exist in its environment.

## Template

`delegate_task`'s `prompt` param is substituted into this template before being (a) written to
`logs/<job_id>/prompt.txt` as an audit trail, and (b) passed as the provider CLI's own prompt
argument (`-p`/`exec <text>`, depending on provider):

```
# CADET Delegated Task
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
```

## `context_id` conventions

- `context_id` is intentionally opaque/free-text (e.g. `task_refactor_auth_01`), matching the
  convention SALTMDB itself uses on the human/Claude Code side — but a delegated job never queries
  or writes SALTMDB directly, so this value is metadata only from the job's own point of view: it
  exists so a human (or Claude Code, reading `get_task_output`/CADET's job store) can correlate
  several `delegate_task` calls as belonging to the same logical task, and so Claude Code itself can
  later fold a delegated job's real findings into SALTMDB under a consistent thread.
- If the caller doesn't supply one, `delegate_task` generates `"cadet-<uuid4hex[:8]>"` and returns
  it; the caller should treat the returned value as authoritative for any follow-up jobs on the
  same thread.
- **One `context_id` may span many `delegate_task`/`job_id` calls, but continuity across those jobs
  is the caller's (Claude Code's) job, not the template's.** Each delegated job is a cold session
  with no memory of earlier jobs and no way to query SALTMDB itself — if a later job needs an
  earlier job's findings, the caller must fold that content into the later job's own `prompt` text.

## Worked example

Given `delegate_task(prompt="Refactor the auth middleware to use the new token validator",
context_id="task_refactor_auth_01", cwd="C:\\repos\\myapp", label="auth-refactor-step1")` (default
`agy` identity), the rendered prompt written to `logs/job-a1b2c3d4e5f6/prompt.txt` and passed to the
provider CLI is:

```
# CADET Delegated Task
You are Antigravity (agy), running headless via CADET on behalf of Claude Code (the architect
agent for this project). There is no human available to answer follow-up questions in this
session — proceed autonomously using your best judgement and explicitly document any assumptions
you make in your final summary.

## CADET Job Metadata
- Context ID: `task_refactor_auth_01`
- Agent ID: antigravity
- Job label: auth-refactor-step1
- Working directory: C:\repos\myapp
- CADET job id: job-a1b2c3d4e5f6  (informational only — you have no CADET or SALTMDB/MCP tools in
  this environment; do not attempt to call any, including search_memory/log_event/store_memory.
  Just write your complete answer as your final plain-text response.)

## Your Task
Refactor the auth middleware to use the new token validator
```
