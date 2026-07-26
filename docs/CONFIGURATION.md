# CADET Configuration

> Status: design-only — see [ARCHITECTURE.md](./ARCHITECTURE.md) for the on-disk layout these
> variables control.

Env vars only, no config file — mirrors SALTMDB's own established precedent
(`SALTMDB_DB_PATH` defaulting under `~/.saltmdb`, in `src/saltmdb/config.py`).

| Var | Default | Purpose |
|---|---|---|
| `CADET_AGY_PATH` | none — must resolve | Absolute path to the `agy` executable. |
| `CADET_DEFAULT_CWD` | none | Fallback project root used by `delegate_task` when its `cwd` param is omitted. |
| `CADET_STATE_DIR` | `~/.cadet` | Root directory for `state/cadet.db` and `logs/<job_id>/`. |
| `CADET_MAX_CONCURRENT` | `2` | Maximum concurrent `agy` subprocesses; extra jobs queue as `pending`. |
| `CADET_DEFAULT_TIMEOUT_S` | `1800` | Default per-job timeout when `delegate_task`'s `timeout_s` is omitted. |
| `CADET_MAX_TIMEOUT_S` | `7200` | Hard cap — a caller-requested `timeout_s` above this is silently clamped. |
| `CADET_LOG_RETENTION_DAYS` | `14` | Terminal jobs' log directories and DB rows older than this are swept on startup and by a periodic in-process sweep (an `asyncio.sleep` loop — no external scheduler needed). |
| `CADET_AGY_MODEL` | none (agy's own default) | Passed through as `agy --model` unless overridden per-call by `delegate_task`'s `model` param. E.g. `gemini-3.6-flash-medium`. |
| `CADET_AGY_EFFORT` | none (agy's own default) | Passed through as `agy --effort` unless overridden per-call. One of `low`\|`medium`\|`high`. |
| `CADET_AGY_SANDBOX` | `true` | Whether to pass `agy --sandbox` on every launch. See [JOB_LIFECYCLE.md](./JOB_LIFECYCLE.md#process-management) for why this defaults on — and [ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-cli-behavior) for why it's weaker protection than the name implies (silently defeated by `skip_permissions=True`; blocks routine commands outright on Windows without matching `unsandboxed(...)` grants). |
| `CADET_AGY_SETTINGS_PATH` | `~/.gemini/antigravity-cli/settings.json` | Where `cadet-install-agy-permissions` reads/writes `agy`'s permission config. Override mainly for testing — `agy` itself has no flag to point at an alternate settings file, so this only affects CADET's own tooling, not what `agy` actually reads at runtime. |

## Setup step: `cadet-install-agy-permissions`

A manual, one-time, idempotent console script (`pyproject.toml`'s `[project.scripts]`) that
additively merges a curated `permissions.allow` list — read-only git inspection plus running the
delegated repo's test suite, see `src/cadet/process/agy_permissions.py` for the exact list — into
`agy`'s real settings.json, so that ordinary CADET jobs (tests, `git status`/`log`/`show`) don't
need `skip_permissions=True` at all. Run it once after installing CADET:

```
cadet-install-agy-permissions          # merges, reports what was added vs. already present
cadet-install-agy-permissions --check  # reports drift without writing; exit 1 if anything's missing
```

Never removes or overwrites an existing entry (curated or user-added) and never touches any
other top-level key in the settings file. Not run automatically by the MCP server — see
[ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-cli-behavior) for why (mutating a file outside
the repo on every server start would be more invasive than warranted) and for the empirical
findings (two-gate `command`/`unsandboxed` permissions, literal-only rule matching) this list is
built from.

## `CADET_AGY_PATH` must be absolute

MCP-launched server processes often don't inherit the full interactive-shell `PATH` — the same
gotcha SALTMDB's own install docs call out for resolving `python`. CADET should resolve
`CADET_AGY_PATH` once at startup and **fail fast with a clear error** (not a silent no-op) if it
doesn't point to an existing, executable file, rather than deferring the failure to the first
`delegate_task` call.

## Directory layout

```
~/.cadet/                          (or $CADET_STATE_DIR)
  state/cadet.db                   # job store — see JOB_LIFECYCLE.md
  logs/<job_id>/prompt.txt         # rendered prompt actually passed to `agy -p`
  logs/<job_id>/stdout.log
  logs/<job_id>/stderr.log
```

## Log retention

On startup, and periodically thereafter (an in-process background loop — no cron/external
scheduler), CADET sweeps: any job row in a terminal state (`succeeded`, `failed`, `timeout`,
`cancelled`, `unknown-interrupted`) whose `finished_at` is older than `CADET_LOG_RETENTION_DAYS`
has its `logs/<job_id>/` directory deleted and its DB row removed. `pending`/`running` rows are
never swept regardless of age.

## No pre-flight quota visibility

There is no `agy` CLI command or flag that reports remaining Antigravity subscription quota before
dispatching a job — it's only visible in the interactive TUI, and CADET cannot check it ahead of
time. It *can*, however, detect quota exhaustion after the fact: a job that fails because a quota
pool (Gemini or Claude models are tracked separately) is depleted gets `error_kind:
"quota_exhausted"` and a parsed `quota_reset_at` timestamp on its record (see
[ARCHITECTURE.md](./ARCHITECTURE.md#quota-exhaustion-detection)) — CADET does not, however, act on
this itself (no auto-pausing dispatch); it's exposed for Claude to decide policy on.

## Single-instance assumption

Running two CADET server processes against the same `CADET_STATE_DIR` simultaneously is
unsupported and undefined — no file-locking or multi-writer coordination is designed for this (see
[ARCHITECTURE.md](./ARCHITECTURE.md#non-goals)). If you need to run multiple CADET instances,
give each its own `CADET_STATE_DIR`.
