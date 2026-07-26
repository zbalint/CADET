# CADET Web Dashboard

A small local dashboard for watching `delegate_task` jobs without going through Claude: ongoing
tasks, finished tasks, per-task detail (metadata + stdout/stderr logs), and a Cancel action for
pending/running tasks. Read-mostly — there's no "launch a new task" route; `delegate_task` remains
an MCP-only operation.

## Why it runs in-process, not as a separate server

`Dispatcher.cancel()` (`src/cadet/jobs/dispatcher.py`) has two paths. Cancelling a `pending` job is
a pure DB write, safe from anywhere. Cancelling a `running` job is not: it adds the job to an
**in-memory set** (`Dispatcher._cancel_flags`) on the live `Dispatcher` instance and tree-kills the
process; the terminal status (`cancelled` vs `failed`) is decided later by that same job's own
`run_job` completion path checking that in-memory flag. A cancel issued against a *different*
`Dispatcher` instance — e.g. from a separate process reading the same SQLite file — would kill the
right PID but the job would come back marked `failed`, not `cancelled`.

So the dashboard's HTTP server is started as an `asyncio.create_task(...)` inside
`server_lifespan` (`src/cadet/mcp/server.py`), alongside the existing retention-sweep task,
sharing the exact same `Dispatcher` object and event loop as the MCP stdio server. Two consequences
worth knowing:

- If the dashboard fails to bind (e.g. the port is already in use by another `cadet-server`), it
  logs a warning and the process keeps running — the MCP tool surface is the primary contract and
  must not go down because the dashboard couldn't start.
- Uvicorn's own SIGINT/SIGTERM/SIGBREAK handling is disabled (`_EmbeddedServer.capture_signals`
  override in `src/cadet/web/server.py`) so it doesn't hijack process termination for the whole
  `cadet-server` process, and its logging is routed through the same stderr-only logger CADET
  already uses (`log_config=None`) — stdout is the MCP JSON-RPC transport and must never carry
  anything else.

## Security default

There is no authentication anywhere in CADET. Since the dashboard exposes job prompts/logs
(potentially sensitive) and a cancel action, it binds to `127.0.0.1` only by default. Override via
`CADET_WEB_HOST` only if you understand the exposure — see [CONFIGURATION.md](./CONFIGURATION.md).

## Routes

| Route | Method | Notes |
|---|---|---|
| `/` | GET | Serves the static dashboard (`src/cadet/web/static/index.html`). |
| `/api/health` | GET | Liveness check. |
| `/api/tasks` | GET | `status_filter`, `context_id`, `limit` query params — same shape as the `list_tasks` MCP tool. |
| `/api/tasks/{job_id}` | GET | Same shape as `check_task_status`. 404 if unknown. |
| `/api/tasks/{job_id}/output` | GET | `tail_lines` query param — same shape as `get_task_output`. 404 if unknown. |
| `/api/tasks/{job_id}/cancel` | POST | Same shape as `cancel_task`. Idempotent on terminal jobs. 404 if unknown. |

Response shaping is shared with the MCP tools via `src/cadet/status.py` (`shape_status_dict`,
`pending_queue_position`, `read_log`) — one source of truth for what a "task status" looks like.

## Frontend

Plain HTML/CSS/JS (`src/cadet/web/static/`) — no build step, no framework. The page polls
`/api/tasks` on an interval to refresh the list, and polls `/api/tasks/{id}` +
`/api/tasks/{id}/output` on a separate interval only while a task's detail pane is open.
