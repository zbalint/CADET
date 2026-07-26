# CADET MCP Tool Surface

> Status: design-only — see [ARCHITECTURE.md](./ARCHITECTURE.md) for context. Five tools total,
> deliberately minimal (see "Deliberately excluded" below).

## FastMCP argument convention

CADET follows the same tolerant-argument pattern already established by SALTMDB
(`src/saltmdb/mcp/tools.py`), and CADET's tools should copy those helpers verbatim rather than
reinvent them:

- Every tool is declared as `def name(explicit_param=None, ..., **kwargs) -> dict`. FastMCP emits
  a required `kwargs` schema field for bare `**kwargs` params, so some MCP clients pass args
  nested under a literal `kwargs={...}` key — the `_unwrap_kwargs(kwargs)` helper flattens that.
- `_resolve(explicit, kw, raw_kwargs, *aliases)` picks the first non-`None` value among the
  explicit param, the unwrapped kwargs dict, and one or more alias keys (e.g. `context_id` /
  `project_id` / `project`), so callers that use slightly different field names still work.
- Callers (Claude, `agy`, or anything else) that get a "missing required field: kwargs" schema
  error should just retry with `kwargs={}` appended — this is expected FastMCP behavior, not a bug.

## Error shape

Validation failures return `{"error": "<message>"}` rather than raising — this keeps the tool
contract simple and lets Claude branch on `"error" in result` without needing to catch exceptions
across the MCP boundary.

## Tools

### `delegate_task`

Starts a background job running `agy -p "<rendered prompt>"`. Never blocks on the subprocess —
returns as soon as the job is queued/dispatched.

**Params:**
| Name | Type | Default | Notes |
|---|---|---|---|
| `prompt` | str | required | Raw task description (gets wrapped in the standard template — see [PROMPT_PROTOCOL.md](./PROMPT_PROTOCOL.md) — before being handed to `agy`). |
| `context_id` | str | auto-generated | If omitted, CADET generates `"cadet-<uuid4hex[:8]>"` and returns it. Callers should treat the returned value as authoritative and reuse it for related follow-up jobs. |
| `cwd` | str | `CADET_DEFAULT_CWD` | Validated synchronously (`os.path.isdir`) before returning. Invalid `cwd` returns `{"error": ...}` immediately rather than creating a doomed job. |
| `timeout_s` | int | `CADET_DEFAULT_TIMEOUT_S` | Clamped to `CADET_MAX_TIMEOUT_S`. Also passed through to `agy` as `--print-timeout` (see [JOB_LIFECYCLE.md](./JOB_LIFECYCLE.md)). |
| `label` | str | `None` | Free-text, non-unique, purely for human/Claude readability in `list_tasks`. |
| `model` | str | `CADET_AGY_MODEL` | Passed through as `agy --model`. Lets a caller pin a specific tier (e.g. `gemini-3.6-flash-low` for trivial work vs `-high` for harder tasks) instead of always using the configured default. |
| `effort` | str | `CADET_AGY_EFFORT` | Passed through as `agy --effort` (`low`\|`medium`\|`high`). |
| `skip_permissions` | bool | `false` | Passed through as `agy --dangerously-skip-permissions` when `true`. Default `false` deliberately — see [ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-cli-behavior). **This is stronger than "removes the soft-deny":** confirmed via `google-antigravity/antigravity-cli#36` (open/unpatched) that it also silently defeats CADET's `--sandbox` flag entirely — with `skip_permissions=True` there is effectively **zero** containment, not partial. Run `cadet-install-agy-permissions` (see [CONFIGURATION.md](./CONFIGURATION.md)) first — most ordinary CADET jobs (tests, read-only git) shouldn't need `skip_permissions=True` at all once the curated allow-list is installed. Only set `true` for tasks in a `cwd` you're comfortable giving fully unrestricted tool access to. |

**Returns:** `{job_id, status, context_id, queue_position, label, created_at}`
- `status` is `"pending"` or `"running"` depending on whether a concurrency slot was free.
- `queue_position` is `null` unless `status == "pending"`.

### `check_task_status`

Cheap poll: a single SQLite row read, **no log file I/O**. Safe to call frequently.

**Params:** `job_id` (str, required).

**Returns:** `{job_id, label, context_id, status, model, created_at, started_at, elapsed_s, exit_code, error_kind, quota_reset_at, timeout_s, queue_position}`
- `elapsed_s` = `now - started_at` while `running`, `finished_at - started_at` once terminal,
  `null` while still `pending`.
- `error_kind` is `null` for non-failed jobs, or a best-effort classification such as
  `"quota_exhausted"` (see [ARCHITECTURE.md](./ARCHITECTURE.md#quota-exhaustion-detection)) —
  absence of a classification does not mean the failure is understood, only that it didn't match a
  known pattern.
- `quota_reset_at` (ISO8601) is set only alongside `error_kind == "quota_exhausted"`; `model` tells
  you which of Antigravity's quota pools that reset applies to.

### `get_task_output`

Reads log files. Safe to call on a still-running job — it peeks at whatever's been flushed so far
(useful for progress visibility before completion).

**Params:**
| Name | Type | Default | Notes |
|---|---|---|---|
| `job_id` | str | required | |
| `tail_lines` | int | `None` (full output) | If set, returns only the last N lines of each stream. |

**Returns:** `{job_id, status, stdout, stderr, exit_code, error_kind, quota_reset_at, duration_s, truncated, stdout_log_path, stderr_log_path}`
- Log paths are always included (free — already tracked in the job store) as an escape hatch: if
  `tail_lines` isn't enough, Claude can `Read` the full file directly.
- `error_kind`/`quota_reset_at` mirror `check_task_status` — see
  [ARCHITECTURE.md](./ARCHITECTURE.md#quota-exhaustion-detection).

### `list_tasks`

**Params:**
| Name | Type | Default | Notes |
|---|---|---|---|
| `status_filter` | str | `None` | One of the job states (see [JOB_LIFECYCLE.md](./JOB_LIFECYCLE.md)). |
| `context_id` | str | `None` | Filter to jobs under one SALTMDB thread. |
| `limit` | int | 20 | Hard cap 200. |

**Returns:** list of rows shaped identically to `check_task_status`'s return, sorted `created_at desc`.

### `cancel_task`

Idempotent — calling on an already-terminal job is a no-op that reports the existing terminal
status rather than erroring.

**Params:** `job_id` (str, required).

**Returns:** `{job_id, previous_status, status, already_terminal}`

## Deliberately excluded from v1

- A raw-prompt bypass flag (skip the standard template wrapping) — not needed until a concrete use
  case demands it.
- A separate "task detail" tool — `get_task_output`'s log paths already give Claude a direct
  escape hatch to read full logs itself.
- A `resume_conversation_id` param wired to `agy --continue`/`--conversation` — `agy` supports
  native conversation resumption, which could give real continuity across jobs under one
  `context_id` instead of relying on `agy` cold-reading SALTMDB every time. Not included because
  it's unverified whether `-p` (print) mode exposes a capturable conversation ID after a run — see
  [ARCHITECTURE.md](./ARCHITECTURE.md#open-questions--risks-unresolved--flagged-for-whoever-implements-next).

All three are easy, low-risk additions later if the underlying question is resolved — not built
now (YAGNI).

## Companion script: `cadet-wait-for-job` (not an MCP tool)

The "five tools total, deliberately minimal" framing above is about the MCP tool
surface specifically. Separately, CADET also ships a plain console script,
`cadet-wait-for-job <job_id>` (see
[CONFIGURATION.md](./CONFIGURATION.md#companion-script-cadet-wait-for-job)), callable
only as a subprocess — never registered as an `@mcp.tool()`, never part of this
surface. It exists to solve a different problem than any of the 5 tools above:
`delegate_task` never blocks, so the calling agent must remember to poll; this script
lets Claude Code's own `Bash(run_in_background=True)` mechanism do that
waiting/notifying instead of relying on the agent remembering to call
`check_task_status` itself.
