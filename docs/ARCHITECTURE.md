# CADET Architecture

> Status: design-only. No server code exists yet — this document (and its siblings in `docs/`)
> is the spec a future implementation session should build directly from.

## Why CADET exists

Claude Code is a strong architect but expensive to run for bulk execution work. Antigravity
(`agy`), Google's agentic CLI running Gemini Flash models, is cheap and fast but a weaker
architect — as are the other free/cheap-tier CLI coding agents CADET is being widened to support
(Codex CLI, Cursor CLI, GitHub Copilot CLI; see "Provider abstraction" below). CADET lets Claude
plan once and delegate the resulting implementation/execution work to a chosen provider, instead
of paying Claude-level cost for grunt work.

## Actors

```
 Claude Code  ──MCP──▶  CADET  ──subprocess──▶  <provider CLI> -p/-exec "<rendered prompt>"
      │                                                  │
      └───────────────────MCP──────┐    ┌────MCP─────────┘
                                    ▼    ▼
                                 SALTMDB (shared memory)
```

- **Claude Code** — the architect. Decomposes work, calls CADET's tools to delegate a task
  (picking a `provider` — default `agy`), and later polls for status/output. Also has its own
  SALTMDB tools.
- **CADET** — an MCP server (Python + FastMCP) that Claude Code connects to. Its only job is
  process orchestration: render a prompt, spawn the chosen provider's CLI as a background
  subprocess, track its lifecycle, and expose status/output back to Claude via MCP tools. **CADET
  never calls SALTMDB.**
- **The provider CLI** (`agy`/Antigravity today; Codex, Cursor, Copilot planned) — the executor.
  Invoked headless via its own non-interactive flag (`agy -p "<prompt>"`). Has its own SALTMDB MCP
  tools and its own global instructions, near-identical to Claude's, telling it to search/store
  memory and log events under a shared `context_id`.
- **SALTMDB** — an existing, separate MCP memory server (`C:\Users\zbalint\Workspace\SALTMDB`,
  not modified by this project). Both Claude and the delegated provider connect to it
  independently. It is how knowledge actually flows between the two agents — CADET only carries
  the `context_id` that ties a given job back to the right SALTMDB thread.

## Provider abstraction

CADET delegates to a pluggable set of CLI coding agents rather than being hardcoded to `agy`.
Each provider is a plain Python module under `src/cadet/process/providers/` exposing `NAME`,
`AGENT_ID`, `DISPLAY_NAME`, `build_argv(...)`, `spawn(...)`, and `parse_error(...)` — see
`providers/agy.py` for the reference shape (a thin re-export shim over the original
`launcher.py`/`quota.py`, kept as the source of truth for agy's own behavior). `delegate_task`'s
`provider` param (default `"agy"`) selects which module handles a job; `src/cadet/process/
providers/registry.py` is the single source of truth for which provider names are known.

**`agy`, `codex`, and `cursor` are wired up today.** GitHub Copilot CLI is planned as a separate
follow-up phase, gated on an empirical validation pass against the real installed binary — vendor
docs leave real ambiguity about whether a CLI's default invocation actually applies edits or
silently no-ops. It's an accepted outcome if a given provider ends up only safely usable for
read-only/analysis tasks initially — this is exactly what happened with `codex`'s default (see
"Validated `codex` CLI behavior" below): its safe default is read-only, and real edits require
`skip_permissions=True` on this platform until an upstream Windows bug is fixed. `cursor`'s
situation is subtler still (see "Validated `cursor` CLI behavior" below): its safe read-only lever
(`--mode plan`) genuinely works, but the "just apply edits, still gate risky commands" middle
ground doesn't — confirmed via a live invocation that silently declined a file write while its own
final message dishonestly claimed success.

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
  provider          TEXT NOT NULL DEFAULT 'agy',  -- which provider ran this job (see "Provider abstraction" above)
  model             TEXT,               -- requested `--model` (or provider equivalent), if any (see MCP_TOOLS.md)
  effort            TEXT,               -- requested `--effort` (or provider equivalent), if any
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

## Validated `codex` CLI behavior

Confirmed empirically against the installed CLI (`codex` CLI, Windows), on 2026-07-26 — real
invocations against a scratch directory, not vendor docs alone:

- **`codex exec "<prompt>" -C <cwd> -s workspace-write` silently no-ops on Windows.** Exit code
  `0`, but the target file was never written. `codex`'s own stderr revealed why:
  `windows sandbox: orchestrator_helper_launch_failed: ... helper=codex-windows-sandbox-setup.exe,
  error=program not found` — the OS-level write-enforcement helper `workspace-write` depends on
  isn't launching in this install. `codex` itself detects the failure and reports it in its
  final message, but the **process exit code stays 0** — the same false-success shape already
  documented for `agy` above, just with a different root cause. **Do not treat
  `workspace-write` as functional for real edits on this platform** until the helper issue is
  fixed upstream (or a working install is confirmed).
  - **Community workaround tried and reverted (2026-07-26, `openai/codex#23194`).** Copying
    `codex-windows-sandbox-setup.exe` from the release's `codex-resources\` dir into `bin\`
    (alongside `codex.exe`) does get past the "program not found" error, but only surfaces a
    *deeper* failure: `windows sandbox: CreateProcessWithLogonW failed: 2` — and, worse, `codex`'s
    own final message then falsely claims success (e.g. "Created `hello.txt`") even though the
    file was never written. That's a strictly worse failure shape (slow hang + false-positive
    claimed outcome, vs. a fast clean no-op) for no actual gain, and multiple other upstream
    issues report the same unresolved Windows sandbox problem — so the workaround was reverted.
    **Do not reapply this workaround without confirming upstream has actually fixed the deeper
    `CreateProcessWithLogonW` issue first.**
- **`-s read-only` runs cleanly** (vendor default) — confirmed via a real invocation that doesn't
  attempt any writes. Safe, but by definition cannot apply edits.
- **`--dangerously-bypass-approvals-and-sandbox` is the only flag confirmed to actually apply
  edits headlessly.** Reproduced directly: `codex exec "create hello.txt..." -C <scratch-dir>
  --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check` produced the file with the
  exact requested content, exit code `0`. CADET maps this to the existing `skip_permissions`
  param (mirrors `agy`'s `--dangerously-skip-permissions`) — **`codex` jobs are read-only unless
  the caller explicitly opts into `skip_permissions=True`,** since the "safe middle ground"
  (`workspace-write`) doesn't work on this platform yet.
- **`codex exec` always attempts to read stdin**, printing `Reading additional input from
  stdin...` even when the prompt is passed as an argv argument (not just when no prompt is
  given). This matters specifically because CADET spawns it as a long-lived subprocess of its own
  MCP server — without an explicit redirect, the child would inherit the server's own stdio
  transport pipe as its stdin. `providers/codex.py`'s `spawn()` explicitly sets
  `stdin=subprocess.DEVNULL` to guarantee immediate EOF and rule this out; confirmed via a live
  run that `/dev/null` stdin doesn't hang or change behavior.
- **`-m/--model` and `-c model_reasoning_effort=<value>` both confirmed to parse and take
  effect** via a real invocation (`-m gpt-5.6-terra -c model_reasoning_effort=low`, matching the
  field name already used in the user's own `~/.codex/config.toml`).
- **No confirmed quota-exhaustion wording.** Unlike `agy`'s reproduced "Individual quota reached"
  string, no real quota exhaustion was observed during validation (forcing one would burn real
  quota). `providers/codex.py`'s `parse_error` has a best-effort regex based on community reports
  of "usage limit reached" phrasing, explicitly marked unconfirmed in its docstring — same
  opportunistic-enrichment posture as `agy`'s parser, not a correctness requirement.

## Validated `cursor` CLI behavior

Confirmed empirically against the installed CLI (`cursor-agent`, Windows, free-tier account), on
2026-07-26 — real invocations against scratch directories, not vendor docs alone:

- **Critical — `CADET_CURSOR_PATH` must point at `cursor-agent.ps1`, invoked via `powershell.exe
  -File`, not at the vendor's own `cursor-agent.cmd` invoked directly.** The `.cmd` is a thin
  `cmd.exe` batch wrapper (`... %*`) around the real `cursor-agent.ps1`. Exec'ing a `.cmd` file
  directly via `asyncio.create_subprocess_exec` on Windows routes it through `cmd.exe`, which
  re-parses the whole command line for its own metacharacters (`<`, `>`, `&`, `|`, `^`) before the
  batch script's `%*` ever sees it — this is `cmd.exe`'s line-level redirection parsing, not shell
  string interpolation, so it happens regardless of how carefully the argv list itself is quoted.
  CADET's own rendered prompt template (`prompt/template.py`) contains a literal `<one-line
  summary + outcome>` placeholder — **every real `delegate_task` call for `cursor` silently
  corrupted its own argv** when routed through the `.cmd`. Reproduced directly: a trivial
  ASCII-only prompt worked fine through the `.cmd`, but the real rendered prompt — through the
  identical `spawn()` code path — made `--trust` effectively disappear, producing the exact
  "Workspace Trust Required" exit-1 failure described below on every single job. Invoking
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File <cursor-agent.ps1 path> <same argv>`
  instead — bypassing `cmd.exe` entirely — was confirmed via a live run with the actual rendered
  prompt: file written with correct content, real SALTMDB tool calls executed per the prompt's own
  instructions. `-File` forwards each argument to the script literally; PowerShell does not
  re-interpret `<`/`>` there. `cursor-agent.ps1` itself dynamically resolves whatever the latest
  installed version under `<install-dir>\versions\<version>\` is, so this still survives `cursor-
  agent update` (unlike hardcoding a specific version's `node.exe`+`index.js` pair directly).
- **Real jobs can take noticeably longer to exit than to finish their visible work.** One
  confirmed run: the model's conversation (including real SALTMDB tool calls) completed and the
  target file was written well under a minute in, but `proc.wait()` didn't resolve until ~47s
  later. Not an infinite hang — it did return, exit code `0` — and CADET's own per-job `timeout_s`
  + `kill_process_tree` already covers the worst case regardless, same as any other provider.
- **A directory `cursor-agent` has never seen before needs BOTH `--trust` and `--force`/`--yolo`
  together for a real edit to apply — neither flag alone is reliable.** The very first-ever
  headless invocation on this machine (no flags at all) printed an explicit "Workspace Trust
  Required" banner and exited `1`. After that, new directories no longer show the banner (a
  one-time, machine-wide onboarding gate, not a per-directory one), but `--force`/`--yolo` alone on
  a brand-new directory still silently declined the edit — exit `0`, no file written, **and the
  model's own final text falsely claimed success** (e.g. "Created `foo.txt`..."). Reproduced as a
  clean A/B on identical fresh directories: `--force` alone failed; adding `--trust` alongside it
  made the same edit actually land. `providers/cursor.py`'s `build_argv` always passes `--trust`
  unconditionally for this reason.
- **`--mode plan` is the genuinely safe/read-only lever — not `--sandbox`.** `--mode plan`
  confirmed to produce no file write and no false success claim, even with `--trust --force` also
  present. `--sandbox enabled` (the vendor's own OS-level sandbox toggle), by contrast, hard-errors
  immediately on Windows: `Error: Sandbox mode is enabled but not available on this system.
  Sandbox requires macOS or Linux.` — never passed by CADET on this platform.
- **The "safe-but-functional" middle ground (`sandbox=False`, `skip_permissions=False`) does not
  work headlessly, the same "known non-functional combo" class as `codex`'s broken
  `workspace-write` on Windows** — just for a different reason (no TTY to approve the edit, rather
  than a missing OS helper). `providers/cursor.py` maps `skip_permissions=True` to `--force`
  (real edits, confirmed working when paired with the unconditional `--trust`) and falls back to
  `--mode plan` whenever `sandbox=True`; the `sandbox=False, skip_permissions=False` combo is left
  as a documented non-functional caveat rather than worked around.
- **`--model` must always be passed explicitly — never omitted.** Vendor behavior: omitting
  `--model` doesn't fall back to a stateless CLI default, it inherits whatever model string was
  last selected *globally* across every `cursor-agent` invocation on the machine, persisted in
  `~/.cursor/cli-config.json`. Confirmed the hard way during validation: passing an
  account-unavailable named model once made that same model the sticky default for every
  subsequent call — including plain interactive use outside CADET entirely — until explicitly
  reset with `--model auto`. `providers/cursor.py` defaults to `"auto"` (the only model available
  on free-tier accounts) whenever CADET's own `model` param is unset, specifically to avoid ever
  relying on that ambient global state.
- **Effort has no dedicated flag** — vendor docs describe bracket overrides on the model string
  itself (e.g. `'claude-opus-4-8[context=1m,effort=high]'`). `providers/cursor.py` applies this
  syntax (`f"{model}[effort={effort}]"`) only when both are set. **UNCONFIRMED against a real
  call** — this account is free-tier and can only use `auto`, which the bracket syntax was never
  validated against.
- **No structured quota-exhaustion signal was found** — this account never hit a real rate limit
  during validation. `providers/cursor.py`'s `parse_error` is best-effort only, same unconfirmed
  posture as `codex`'s.

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

## The polling-forgetfulness problem

`delegate_task` never blocks (by design — see above), which means the calling agent
(Claude Code) is solely responsible for remembering to poll `check_task_status`/
`get_task_output` afterward. In practice this has been forgotten for extended
periods. CADET addresses this **outside** the MCP tool surface: a companion console
script, `cadet-wait-for-job` (see
[MCP_TOOLS.md](./MCP_TOOLS.md#companion-script-cadet-wait-for-job-not-an-mcp-tool)
and [CONFIGURATION.md](./CONFIGURATION.md#companion-script-cadet-wait-for-job)),
which Claude Code backgrounds via its own `Bash` tool right after `delegate_task`,
piggybacking on the harness's existing Bash-background auto-notify behavior rather
than CADET building a new notification channel of its own.

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
7. **Quota-exhaustion wording is only empirically confirmed for `agy`.** `codex`'s and `cursor`'s
   `parse_error` (and any future `copilot` equivalent) are best-effort guesses at vendor wording,
   never validated against a real exhausted-quota failure — forcing one deliberately to test it
   wasn't attempted, since it burns real quota. Until each provider has its own confirmed
   reproduction (the next time one is naturally exhausted during real usage — capture the exact
   stderr then), a `null` `error_kind` on a failed non-agy job does **not** mean "definitely not a
   quota issue" — it may just mean the guessed regex didn't match. Check raw `stderr` via
   `get_task_output` rather than trusting `error_kind` alone for non-agy providers in the meantime.
8. **`cursor`'s "safe-but-functional" edit mode is unreachable headlessly.** Unlike `codex` (whose
   broken middle ground is a Windows-specific upstream bug that could plausibly be fixed
   upstream), `cursor`'s gap looks structural: real edits require bypassing tool-call approval
   entirely (`--force`), and there is no confirmed way to allow edits while still gating other
   risky actions without a TTY present. Revisit if a future `cursor-agent` version adds a
   headless-safe approval channel.
