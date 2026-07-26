# CADET
**C**laude-**A**gents **D**elegation & **E**xecution **T**ool

CADET is a lightweight MCP server allowing Claude Code to delegate high-speed execution and
long-context refactoring tasks to pluggable CLI coding agents, via a `provider` param on
`delegate_task`. Today only one provider is actually wired up — `agy` (Antigravity CLI) — with
Codex CLI, Cursor CLI, and GitHub Copilot CLI planned as separate follow-up phases (see
`docs/ARCHITECTURE.md`).

> **Status: alpha.** The MCP server and web dashboard are implemented; see the docs in
> [`docs/`](./docs) for the full architecture spec.

## How it fits together

Claude Code is a strong architect but expensive to run for bulk execution work. Antigravity
(`agy`), running cheap/fast Gemini Flash models, is a capable executor but a weaker architect —
and so are the other free/cheap-tier CLI agents CADET is being widened to support. CADET is the
MCP server Claude Code connects to in order to hand off implementation work to a chosen provider
as background jobs (e.g. `agy -p "<prompt>"`), so Claude plans once and doesn't pay Claude-level
cost for the grunt work. Both agents separately connect to [SALTMDB](../SALTMDB), an existing
shared MCP memory server, and thread their work together via a shared `context_id` — CADET itself
never talks to SALTMDB; it only orchestrates the provider's subprocess and hands it that
`context_id`.

## Documentation

- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — actors, diagram, non-goals, on-disk layout, open risks
- [`docs/MCP_TOOLS.md`](./docs/MCP_TOOLS.md) — the five MCP tools (`delegate_task`, `check_task_status`, `get_task_output`, `list_tasks`, `cancel_task`)
- [`docs/JOB_LIFECYCLE.md`](./docs/JOB_LIFECYCLE.md) — job state machine, dispatcher/concurrency, startup reconciliation
- [`docs/PROMPT_PROTOCOL.md`](./docs/PROMPT_PROTOCOL.md) — how a delegated prompt is templated and handed to `agy`
- [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) — env vars, directory layout, log retention
- [`docs/WEB_DASHBOARD.md`](./docs/WEB_DASHBOARD.md) — local dashboard for viewing/cancelling tasks
