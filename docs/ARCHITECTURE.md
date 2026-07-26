# CADET Architecture

> Status: design-only. No server code exists yet — this document (and its siblings in `docs/`)
> is the spec a future implementation session should build directly from.

## Why CADET exists

Claude Code is a strong architect but expensive to run for bulk execution work. Antigravity
(`agy`), Google's agentic CLI running Gemini Flash models, is cheap and fast but a weaker
architect. CADET lets Claude plan once and delegate the resulting implementation/execution work
to Antigravity, instead of paying Claude-level cost for grunt work.

## Actors

```
 Claude Code  ──MCP──▶  CADET  ──subprocess──▶  agy -p "<rendered prompt>"
      │                                                  │
      └───────────────────MCP──────┐    ┌────MCP─────────┘
                                    ▼    ▼
                                 SALTMDB (shared memory)
```

- **Claude Code** — the architect. Decomposes work, calls CADET's tools to delegate a task, and
  later polls for status/output. Also has its own SALTMDB tools.
- **CADET** — an MCP server (Python + FastMCP) that Claude Code connects to. Its only job is
  process orchestration: render a prompt, spawn `agy` as a background subprocess, track its
  lifecycle, and expose status/output back to Claude via MCP tools. **CADET never calls SALTMDB.**
- **`agy` (Antigravity)** — the executor. Invoked headless via `agy -p "<prompt>"`. Has its own
  SALTMDB MCP tools and its own global instructions (`~/.gemini/GEMINI.md`), near-identical to
  Claude's, telling it to search/store memory and log events under a shared `context_id`.
- **SALTMDB** — an existing, separate MCP memory server (`C:\Users\zbalint\Workspace\SALTMDB`,
  not modified by this project). Both Claude and `agy` connect to it independently. It is how
  knowledge actually flows between the two agents — CADET only carries the `context_id` that ties
  a given `agy` run back to the right SALTMDB thread.

## The `context_id` thread

`context_id` is the single correlation key connecting all three systems:
- Claude picks (or lets CADET generate) a `context_id` and passes it to `delegate_task`.
- CADET bakes that `context_id` into the rendered prompt handed to `agy` (see
  [PROMPT_PROTOCOL.md](./PROMPT_PROTOCOL.md)).
- `agy`, per its own instructions, searches SALTMDB for that `context_id` on startup and logs/
  stores back to it on completion.
- One `context_id` may span multiple `delegate_task` calls (multiple jobs) over time — each `agy`
  process is a cold session with no memory of prior jobs except what it re-reads from SALTMDB
  under that `context_id`.

`job_id` (CADET-internal, one per `delegate_task` call) is a different, narrower identifier than
`context_id` (a SALTMDB thread that may outlive any single job) — don't conflate them.

## Web dashboard

CADET embeds a small read-mostly HTTP dashboard (view ongoing/finished tasks, task detail, cancel)
in the *same* process and asyncio event loop as the MCP stdio server — see
[WEB_DASHBOARD.md](./WEB_DASHBOARD.md) for routes, the in-process constraint, and security
defaults. `agy -p` subprocess remains the only invocation surface; the dashboard is observability
and control over jobs CADET already runs, not a second way to submit work.

## Non-goals

- No sandboxing mechanism built *by* CADET — but CADET does enable `agy`'s own native
  `--sandbox` flag by default for delegated runs (see [JOB_LIFECYCLE.md](./JOB_LIFECYCLE.md)),
  since an unattended, unsupervised job is exactly the scenario that flag exists for. CADET
  doesn't invent containment; it just turns on the containment `agy` already ships. **This is
  weaker than it sounds — see "Validated `agy` CLI behavior" below.** `--sandbox` is silently
  neutralized entirely whenever `skip_permissions=True` is also set (`google-antigravity/
  antigravity-cli#36`, open/unpatched), and on Windows it also blocks routine command execution
  outright (Python, git) regardless of `skip_permissions`, requiring explicit `unsandboxed(...)`
  permission grants instead. `--sandbox` should be treated as a best-effort layer, not a
  guarantee, for any job that isn't fully covered by the curated allow-list
  (`cadet-install-agy-permissions`, see [CONFIGURATION.md](./CONFIGURATION.md)).
- No support for multiple CADET server instances sharing one `CADET_STATE_DIR` — undefined,
  documented as an assumption rather than engineered around.
- No direct CADET↔SALTMDB integration — that connection is entirely `agy`'s and Claude's own
  responsibility, per their respective instructions.
- No quota/subscription-budget management — CADET has no way to check remaining Antigravity
  quota before dispatching a job (see Open questions/risks below); it is not CADET's job to
  ration or throttle usage against a budget it can't observe.

## On-disk layout

```
~/.cadet/
  state/cadet.db           # job store (SQLite)
  logs/<job_id>/prompt.txt  # rendered prompt actually passed to agy -p
  logs/<job_id>/stdout.log
  logs/<job_id>/stderr.log
```

The job store is a single SQLite table (stdlib `sqlite3`, WAL mode) — entirely internal to CADET
and unrelated to SALTMDB's "no raw SQL" rule, which protects `saltmdb.db`'s own redaction/FTS5
triggers specifically, not arbitrary SQLite use elsewhere:

```sql
CREATE TABLE jobs (
  job_id            TEXT PRIMARY KEY,   -- "job-" + uuid4().hex[:12]
  context_id        TEXT NOT NULL,
  label             TEXT,
  prompt_path       TEXT NOT NULL,      -- rendered prompt saved to disk, not inlined in the row
  cwd               TEXT NOT NULL,
  model             TEXT,               -- requested `agy --model`, if any (see MCP_TOOLS.md)
  effort            TEXT,               -- requested `agy --effort`, if any
  skip_permissions  INTEGER NOT NULL DEFAULT 0,  -- 0/1, whether --dangerously-skip-permissions was passed
  pid               INTEGER,            -- null until spawned
  status            TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  started_at        TEXT,
  finished_at       TEXT,
  exit_code         INTEGER,
  timeout_s         INTEGER NOT NULL,
  stdout_log_path   TEXT NOT NULL,
  stderr_log_path   TEXT NOT NULL,
  error_message     TEXT,               -- raw diagnostic text for failed/timeout/unknown-interrupted
  error_kind        TEXT,               -- best-effort classification, e.g. "quota_exhausted"; null otherwise
  quota_reset_at    TEXT                -- ISO8601, parsed from a quota_exhausted job's stderr; null otherwise
);
```

`model`/`effort`/`skip_permissions` record what was actually requested (useful for debugging and,
per the quota note below, for knowing which of Antigravity's quota pools a failure belongs to).
`error_kind`/`quota_reset_at` are populated opportunistically at finalize time — see "Quota
exhaustion detection" below.

See [CONFIGURATION.md](./CONFIGURATION.md) for how `~/.cadet` is overridden, and
[MCP_TOOLS.md](./MCP_TOOLS.md) / [JOB_LIFECYCLE.md](./JOB_LIFECYCLE.md) for the tool surface and
state machine built on top of this schema.

## Validated `agy` CLI behavior

Confirmed empirically against the installed CLI (`agy` v1.1.7, Windows) plus the upstream
`google-antigravity/antigravity-cli` changelog, on 2026-07-26. These are facts the launch
command in [JOB_LIFECYCLE.md](./JOB_LIFECYCLE.md) is built around, not assumptions:

- **`agy -p` supports plain output redirection.** Redirecting stdout/stderr to files works
  correctly on this machine/version — no evidence of the empty-output-under-redirect bug a
  third-party project (`agy-headless-bridge`) claims to work around. Not guaranteed on every
  platform/version, but CADET's plain `asyncio.create_subprocess_exec(..., stdout=file, stderr=file)`
  approach needs no pty/ConPTY wrapper on this setup.
- **Critical — file writes silently go to the wrong place without `--add-dir`.** Running
  `agy -p "create a file..."` with only `cwd` set to a plain, non-registered directory caused
  `agy` to report success (exit 0, a stdout message even including a `file://` link) while
  actually writing the file under `~/.gemini/antigravity-cli/scratch/` instead of the target
  directory — silently, with no error or stderr notice. Re-running the identical task with
  `--add-dir <cwd>` passed explicitly fixed it: the file landed in the correct directory.
  **CADET must always pass `--add-dir <cwd>` on every `delegate_task` launch** — this is a
  correctness requirement, not an optimization; omitting it can produce false-positive "success"
  reports.
- **Headless permission handling (per vendor changelog, not independently reproduced):** as of
  v1.1.3, a headless (`-p`) run that hits a tool call requiring permission it doesn't have no
  longer hangs or silently auto-approves — it **soft-denies** the tool call and prints a stderr
  notice naming the allow-rule that would permit it, while the process exits `0` regardless (see
  the false-success caveat in "Open questions/risks" below — this is a real correctness gap, not
  just cosmetic). As of v1.1.5, headless runs honor a persisted `settings.json` permission/
  sandbox/auto-execution policy.
- **`--dangerously-skip-permissions` silently defeats `--sandbox` entirely (confirmed,
  2026-07-26).** `google-antigravity/antigravity-cli#36` (open, unpatched as of this check):
  when both flags are set, `agy`'s own internal retry-without-sandbox request (`bypassSandbox:
  true`) gets auto-approved by the same blanket flag that approves everything else, so
  `--sandbox` provides **zero** real containment whenever `skip_permissions=True`. Reproduced by
  the reporter: a command run with both flags wrote a file outside the sandboxed workspace.
  Claude Code does not have this flaw by design. **Do not treat `--sandbox` as independent
  protection when `skip_permissions=True` — it isn't.**
- **On Windows, `--sandbox` (AppContainer) blocks routine command execution outright, even
  without `skip_permissions` (confirmed empirically, 2026-07-26).** AppContainer denies broad
  `C:\` access by default, which blocks `python.exe` and `git.exe` on this machine's typical
  install paths. Live-tested: `command(git status)` (an allow-rule already present from the
  user's own interactive usage) still failed headlessly with `exit_code=1`, stderr `granting
  access to C:\: Access is denied.` A `python -m pytest ...` invocation hit a **two-gate**
  sequence instead — first the normal `"command"` permission (soft-denied exactly as described
  above if missing), then, once granted, the sandboxed execution itself fails and `agy` requests
  a *second*, distinct `"unsandboxed"` permission to retry outside the sandbox (soft-denied the
  same way if that's missing too — same empty-stdout/`exit_code=0` false-success symptom). Only
  granting **both** `command(<target>)` and `unsandboxed(<target>)` produced a real, verified
  success (actual pytest output, not just `exit_code=0`).
- **`permissions.allow` rule matching is literal-only, not regex, despite vendor docs (confirmed
  empirically, 2026-07-26).** `antigravity.google/docs/permissions` claims each whitespace token
  is an anchored regex (example given: `command(npm run (build.*))`). Tested directly against
  the installed CLI: neither a token-count-mismatched nor a token-count-matched wildcard rule
  (`command(python -m pytest (.*))`) matched commands that weren't an exact literal string —
  every rule that actually worked, including every pre-existing entry in the real settings.json,
  is a full literal command string. **Design implication:** a curated allow-list must enumerate
  exact invocation strings CADET is known to issue, not a handful of prefix rules.
- **Mitigation implemented:** `cadet-install-agy-permissions` (see
  [CONFIGURATION.md](./CONFIGURATION.md)) additively merges a curated, literal `command(...)` +
  `unsandboxed(...)` allow-list — scoped to CADET's own documented use cases (read-only git
  inspection, running the delegated repo's test suite) — into the real global `settings.json`,
  so ordinary CADET jobs no longer need `skip_permissions=True` at all. It's a manual, one-time,
  idempotent setup step, not run automatically by the MCP server.
- **No CLI-queryable subscription/quota status, but a clean, parseable failure when it's hit.**
  There is no `agy` command or flag that reports remaining subscription quota *before* dispatching
  — it's only visible in the interactive TUI, so CADET cannot pre-flight-check it. But when a
  request is actually blocked by quota, the failure itself is clean: reproduced directly with
  `agy --model claude-sonnet-4-6 --prompt "hello"` against a depleted Claude-model quota, which
  returned **exit code `1`** in ~8s with stdout empty and stderr:
  ```
  Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 94h31m53s.
  ```
  Antigravity tracks (at least) two independent quota pools — one for Gemini models, one for
  Claude models selectable via `--model` — so a `quota_exhausted` failure is scoped to whichever
  pool the job's requested `model` draws from, not global. See "Quota exhaustion detection" below
  for how CADET surfaces this.

## Quota exhaustion detection

Because the failure text above is a real, current, machine-parseable format (not a guess), CADET
does best-effort detection rather than treating every failure as opaque:

- After any job whose process exits non-zero, before finalizing the row, `run_job` scans the
  tail of `stderr.log` for a pattern like `quota reached.*resets in ([0-9dhms]+)` (case-insensitive).
- On a match: parse the duration (`94h31m53s` → seconds) and set `error_kind = "quota_exhausted"`
  and `quota_reset_at = finished_at + parsed_duration` (ISO8601) on the row, alongside the usual
  `failed` status and `exit_code`. `quota_exhausted` is a **label on a `failed` job**, not a new
  top-level state — see [JOB_LIFECYCLE.md](./JOB_LIFECYCLE.md).
- On no match: `error_kind`/`quota_reset_at` stay `NULL` — this is opportunistic enrichment, not a
  correctness requirement. `error_message`/the raw log files remain available regardless via
  `get_task_output`, so nothing is lost if the vendor changes this wording in a future `agy`
  version and the pattern silently stops matching.
- **CADET does not act on this itself** (no auto-pausing dispatch, no remembering reset times
  across jobs) — it only surfaces `error_kind`/`quota_reset_at` as facts on the job record via
  `check_task_status`/`get_task_output`/`list_tasks` (see [MCP_TOOLS.md](./MCP_TOOLS.md)) for
  Claude to act on — e.g. switching a subsequent `delegate_task`'s `model` to the other pool, or
  logging the reset ETA to SALTMDB so other sessions don't rediscover it the hard way.

## Open questions / risks (unresolved — flagged for whoever implements next)

1. **`agy`'s exit code conflates "process didn't crash" with "task actually succeeded."** Beyond
   the confirmed soft-deny behavior above, `exit_code == 0` doesn't guarantee the delegated task
   was fully completed as asked. Claude must still inspect `stdout`/`stderr`/SALTMDB to judge real
   outcome — `get_task_output`'s log paths exist precisely so this inspection is possible.
2. **Windows command-line length limits** on long rendered prompts passed via `agy -p "<prompt>"`.
   Mitigated by always persisting `logs/<job_id>/prompt.txt` regardless of transport, but the
   actual limit needs an empirical test before relying on very long prompts.
3. **Single-instance assumption** — running two CADET servers against the same `CADET_STATE_DIR`
   is unsupported/undefined (no file-locking or multi-writer coordination is designed for this).
4. **No pre-flight quota visibility** — CADET can detect quota exhaustion *after the fact* (see
   "Quota exhaustion detection" above) but still can't check remaining quota before dispatching a
   job. The parsed `Resets in <duration>` format is current as of `agy` v1.1.7 and could change in
   a future version; if it does, detection just silently stops populating `error_kind` rather than
   breaking anything, and the raw stderr text is still visible via `get_task_output`.
5. **`cadet-install-agy-permissions` writes to a single global, shared file.** `agy` has no
   `--settings <path>` flag and no confirmed per-project permission scoping — the curated
   allow-list and the user's own interactive `agy` permission history necessarily live in the
   same `~/.gemini/antigravity-cli/settings.json`. This is an accepted tradeoff (the merge is
   additive-only and never touches unrelated entries), not something CADET currently isolates.
   An unexplored future option: override `HOME`/`USERPROFILE` for the spawned `agy` subprocess
   only, to relocate its whole `~/.gemini` config dir — not attempted, since it would also
   relocate OAuth credentials and other state, a larger and riskier surface than this fix needed.
6. **`--continue`/`--conversation` native session resumption is unused, pending verification.**
   `agy` supports resuming a prior conversation by ID, which could give real cross-job continuity
   under one `context_id` instead of relying solely on `agy` cold-reading SALTMDB each time. Not
   adopted in v1 because it's unverified whether `-p` (print) mode exposes the new conversation's
   ID anywhere capturable by CADET after a run. Worth revisiting once that's confirmed.
