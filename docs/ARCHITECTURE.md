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
- **The provider CLI** (`agy`/Antigravity, Codex, Cursor, or Copilot) — the executor.
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

**`agy`, `codex`, `cursor`, and `copilot` are all wired up today** — each gated on its own empirical
validation pass against the real installed binary before being added, since vendor docs leave real
ambiguity about whether a CLI's default invocation actually applies edits or silently no-ops. It's
an accepted outcome if a given provider ends up only safely usable for read-only/analysis tasks
initially — this is exactly what happened with `codex`'s default (see "Validated `codex` CLI
behavior" below): its safe default is read-only, and real edits require `skip_permissions=True` on
this platform until an upstream Windows bug is fixed. `cursor`'s situation is subtler still (see
"Validated `cursor` CLI behavior" below): its safe read-only lever (`--mode plan`) genuinely works,
but the "just apply edits, still gate risky commands" middle ground doesn't — confirmed via a live
invocation that silently declined a file write while its own final message dishonestly claimed
success. `copilot` (see "Validated `copilot` CLI behavior" below) shares `cursor`'s `.cmd`/`cmd.exe`
prompt-corruption bug — plus has a second, unrelated bug in its `.ps1` sibling, so CADET bypasses
both and invokes `node.exe`/`npm-loader.js` directly — and shares `cursor`'s `--mode plan`
read-only lever, but diverges on the middle ground: `sandbox=False, skip_permissions=False` is not
broken for `copilot` — it just behaves the same as `skip_permissions=True`.

## Containerized `agy` execution

Every provider's own vendor-supplied "sandbox" mechanism turned out to be broken or trivially
bypassable on Windows (see each provider's "Validated ... CLI behavior" section). Rather than
continuing to chase per-vendor Windows bugs, `agy` (CADET's default/most-used provider) was moved
to run inside a local Docker container, whose filesystem/process isolation doesn't depend on any
vendor's cooperation. This replaces `agy`'s native Windows subprocess execution entirely — there is
no dual native/container mode for `agy`. `codex`/`cursor`/`copilot` are untouched by this change.

- **Image**: `docker/agy/Dockerfile` — a minimal `debian:bookworm-slim` base plus `git` and
  `python3`/`pip3` (matching what the curated allow-list actually runs — deliberately not
  `node`/`npm`/`build-essential`, added only if a real delegated task needs them). Extracts the
  official Linux x64 release (`agy_cli_linux_x64.tar.gz` from `google-antigravity/antigravity-cli`
  — the tarball contains a single file named `antigravity`, renamed to `agy` at build time so the
  rest of CADET's argv construction doesn't need a platform branch). Built/tagged locally as
  `cadet-agy:latest` (`docker build -t cadet-agy:latest docker/agy/`) — no registry, single machine
  only.
- **Auth**: a dedicated Docker named volume (`cadet-agy-gemini`, see `CADET_AGY_GEMINI_VOLUME` in
  [CONFIGURATION.md](./CONFIGURATION.md)) holds the containerized `agy`'s own identity, deliberately
  isolated from the host's interactive `~/.gemini` (its large volatile dirs — conversation history,
  cache, crashes — are never shared with the container). **Critical finding: the Linux build of
  `agy` uses an entirely different credential file than Windows** — it looks for
  `~/.gemini/antigravity-cli/antigravity-oauth-token` (confirmed via `strace` tracing its actual
  `openat()` calls), not `oauth_creds.json` (the Windows file, confirmed separately to still work
  fine natively on Windows). No cross-platform credential copy is possible — a one-time interactive
  OAuth login (`docker run --rm -it -v cadet-agy-gemini:/root/.gemini cadet-agy:latest agy -p "..."
  --print-timeout 120s`, pasting the resulting authorization code back into the terminal) must be
  done once against this volume before any headless job can authenticate. `src/cadet/process/
  agy_docker_setup.py` (`cadet-setup-agy-docker` console script) additionally seeds
  `oauth_creds.json`/`google_accounts.json`/`installation_id`/top-level `settings.json`/
  `antigravity-cli/settings.json` from the host — useful for the curated permission allow-list
  (`antigravity-cli/settings.json`) and as a starting point, but **does not by itself satisfy
  auth** — the interactive login step above is still required.
- **Invocation**: `src/cadet/process/launcher.py`'s `build_argv` wraps the same `agy` flags CADET
  always issued (`-p`, `--add-dir`, `--print-timeout`, `--mode accept-edits`, `--sandbox`, `--model`,
  `--effort`, `--dangerously-skip-permissions`) inside `docker run --rm --name <container>`, with
  `-v <cwd>:/workspace -w /workspace` (the target repo bind-mounted — `--add-dir` targets the
  container-side `/workspace` path, never the host `cwd`, to avoid reproducing the exact
  "silent write to the wrong place" bug documented in "Validated `agy` CLI behavior" below),
  `-v cadet-agy-gemini:/root/.gemini`, resource limits (`--memory`, `--cpus`, `--pids-limit`,
  defaults in [CONFIGURATION.md](./CONFIGURATION.md)), and hardening flags (`--cap-drop=ALL`,
  `--security-opt=no-new-privileges`). No `--network none` — `agy` needs outbound HTTPS to
  Gemini's API, so Docker's default bridge network is left alone. The container name is
  deterministic from `job_id` (`cadet-agy-<job_id>`), letting the stop/cancel/reconcile paths target
  it without any extra state.
- **Stop/cancel/reconcile**: the recorded `pid` for an `agy` job is the `docker run` **client's**
  PID, not the container's own lifetime — the container is a daemon-managed object independent of
  that client process. `src/cadet/process/treekill.py`'s `stop_container` (`docker stop --timeout
  <grace_s> <name>`) is the real stop; `kill_process_tree` on the client PID runs afterward as a
  cheap, idempotent defensive fallback. All three of `dispatcher.py`'s kill-call-sites (lost
  `mark_running` race, timeout, `cancel`) and `reconcile.py`'s startup-reconciliation loop are
  provider-aware: `agy` always calls `stop_container` (unconditionally for reconcile — no PID
  liveness pre-check needed, since stopping an already-gone container is itself a tolerated no-op),
  while `codex`/`cursor`/`copilot` are completely unchanged, still going through the original
  PID-based `kill_process_tree` path.
- **Bootstrap**: `config.resolve_agy_docker_image()` replaces the old `resolve_agy_path()` — the
  fail-fast check at server startup is now "is Docker reachable and is `cadet-agy:latest` built"
  (`docker image inspect`) rather than "does a host `agy.exe` exist." `CADET_AGY_PATH` is retired.

## Containerized `codex` execution

Phase 3: the same containerization decision made for `agy` (see above), applied to `codex` —
`codex`'s native Windows execution was already read-only-only in practice (its `workspace-write`
sandbox helper never worked on Windows, see "Validated `codex` CLI behavior" below), so moving it
into a container is a strict improvement, not just consistency for its own sake. Replaces `codex`'s
native Windows subprocess execution entirely — no dual mode. `cursor`/`copilot` are untouched by
this change.

- **Image**: `docker/codex/Dockerfile` — same minimal `debian:bookworm-slim` + `git` +
  `python3`/`pip3` base as `agy`'s image. Extracts the official Linux x86_64 release
  (`codex-x86_64-unknown-linux-musl.tar.gz` from `openai/codex`, release tag `rust-v0.145.0` — note
  the `rust-v` tag prefix, a different convention than agy's bare-version tag; the tarball contains a
  single statically-linked file named `codex-x86_64-unknown-linux-musl`, renamed to `codex` at build
  time). Built/tagged locally as `cadet-codex:latest` (`docker build -t cadet-codex:latest
  docker/codex/`) — no registry, single machine only.
- **Auth**: a dedicated Docker named volume (`cadet-codex-auth`, see `CADET_CODEX_AUTH_VOLUME` in
  [CONFIGURATION.md](./CONFIGURATION.md)) holds the containerized `codex`'s own identity. **Unlike
  `agy`, codex's Windows `~/.codex/auth.json` is directly portable to the Linux container — no
  re-authentication step required.** Confirmed empirically: seeding the volume with just the host's
  `auth.json` (via `src/cadet/process/codex_docker_setup.py` / `cadet-setup-codex-docker`) let a real
  containerized `codex exec` call authenticate and complete successfully with zero interactive login.
  `config.toml` was also confirmed unnecessary for auth+basic exec (A/B tested). A **read-only**
  bind-mount of the host's `~/.codex` does NOT work — `codex` writes to its own config dir even
  during a plain `exec` (PATH-alias bookkeeping) and fails with "Read-only file system"; the volume
  must be writable, same requirement as `agy`'s.
- **Invocation**: `src/cadet/process/providers/codex.py`'s `build_docker_argv` wraps the existing
  pure `build_argv` (unchanged inner CLI-flag logic — `-C /workspace` always targets the
  container-side path now, never a host path) inside `docker run --rm --name <container>`, with
  `-v <cwd>:/workspace -w /workspace`, `-v cadet-codex-auth:/root/.codex`, the same resource-limit and
  hardening flags as `agy`'s container (`--memory`, `--cpus`, `--pids-limit`, `--cap-drop=ALL`,
  `--security-opt=no-new-privileges`), no `--network none`. Container name is deterministic
  (`cadet-codex-<job_id>`), via the same `launcher.container_name_for_job(provider, job_id)` agy
  uses (generalized to take a provider name in Phase 3).
- **Stop/cancel/reconcile**: identical shape to `agy`'s — the recorded `pid` is the `docker run`
  client's, not the container's own lifetime. `providers/codex.py`'s `stop(job_id, pid)` mirrors
  `launcher.stop_agy` exactly. `dispatcher.py`'s stop-fn selection (`Dispatcher._stop_fns()`, a
  provider→stop-fn dict shared by both the timeout/lost-race path and `cancel()` — refactored in
  this same phase so `cancel()` picks up codex's container-stop path too, avoiding the kind of
  latent per-call-site gap `reconcile.py` had during agy's own Phase 2) and `reconcile.py`'s startup
  loop both treat `agy` and `codex` identically now (`provider_name in ("agy", "codex")`).
- **Bootstrap**: `config.resolve_codex_docker_image()` mirrors `resolve_agy_docker_image()` (both
  now share a `_resolve_docker_image(image, build_hint)` helper) — `CADET_CODEX_PATH` (host binary
  path) is retired in favor of Docker image resolution.
- **Known open issue (not `codex`-specific — also affects `agy`)**: cancelling a containerized job
  extremely fast (~0.3s after `delegate_task`, before the container has actually reached "Running")
  can leave an orphaned `Created`-state container behind — Docker's `--rm` auto-remove only fires on
  an exit-from-running event, not on a `docker stop` against a never-started container. Confirmed via
  real testing; not yet fixed in `treekill.stop_container` (a candidate fix is an unconditional
  `docker rm -f` after `docker stop`, not yet implemented). Low real-world likelihood, not blocking.

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

- **No sandboxing mechanism built *by* CADET for `codex`/`cursor`/`copilot`** — for these three
  providers, CADET still only enables the vendor's own native sandbox flag where one exists (see
  their respective "Validated ... CLI behavior" sections below), and every one of those flags has
  been found broken or trivially bypassable on Windows. `agy` is the one exception: as of
  "Containerized `agy` execution" below, CADET now provides its own real containment (a Docker
  container) for `agy` specifically, superseding reliance on `agy`'s own (also broken-on-Windows)
  `--sandbox`/AppContainer mechanism. Extending containerized execution to the other 3 providers is
  a plausible future phase, not yet done.
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

## Validated `agy` container behavior

Confirmed empirically against `cadet-agy:latest` (Docker Desktop, WSL2 backend), 2026-07-26 — real
container invocations, not assumptions:

- **`agy --version` inside the container reports `1.1.7`**, matching the host's native Windows
  install exactly — same release, different platform build.
- **Network egress works from the default bridge network**: `curl` to `oauth2.googleapis.com` and
  `accounts.google.com` from inside the container both returned real HTTP responses (404/302, not
  connection failures), confirming outbound HTTPS to Google's endpoints isn't blocked by Docker
  Desktop's default networking.
- **Auth is genuinely platform-specific — no cross-platform credential copy works.** Tracing
  `agy`'s actual file access with `strace` showed it never once opens `oauth_creds.json` (the file
  copied from the host); it opens `~/.gemini/antigravity-cli/antigravity-oauth-token`, which didn't
  exist anywhere on the host either (the user had simply never run `agy` on Linux before). Confirmed
  the copied Windows credential *is* valid by running `agy` natively on the host in the same
  session — it authenticated fine and hit a quota error, not an auth prompt. A one-time interactive
  OAuth login inside a container attached to the same volume was required and completed
  successfully (confirmed via the resulting quota-exhaustion error replacing the earlier
  auth-timeout error — reaching a quota check requires being authenticated).
- **`docker stop --timeout <n> <name>` cleanly and quickly stops a running container**: tested
  against a container trapping `SIGTERM`, stopped in ~1.3s against a 5s grace period, clean exit 0.
  **Note: `docker stop --time` is deprecated on current Docker CLI versions — use `--timeout`.**
- **Resource/hardening flags (`--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit`)
  don't prevent the container from starting or running ordinary processes** — confirmed via the
  stop-mechanism test above, which ran successfully under all of them.
- **Still unverified, pending the account's quota reset (~67h as of this check) — do not assume,
  confirm before relying on:**
  - Whether `agy`'s own `--sandbox` flag behaves differently (or at all) on Linux vs. Windows
    AppContainer, and whether the curated `command()`/`unsandboxed()` allow-list
    (`cadet-install-agy-permissions`) becomes unnecessary inside the container. Could not be
    observed — this requires a real model call past the auth/quota gate.
  - Whether `--add-dir /workspace` correctness (writing to the bind-mounted host directory, not
    some container-internal path) holds the same way it does on Windows.
  - Whether the outer container's `--cap-drop=ALL`/`--security-opt=no-new-privileges` conflicts
    with anything `agy`'s own sandbox implementation needs internally.
  - Whether the default resource limits (`2g` memory / `2` cpus / `512` pids) are adequate for a
    realistic job (e.g. running a delegated repo's own test suite).

## Validated `codex` container behavior

Confirmed empirically against `cadet-codex:latest` (Docker Desktop, WSL2 backend), 2026-07-27 — real
container invocations, not assumptions:

- **`codex --version` inside the container reports `codex-cli 0.145.0`**, matching the host's native
  Windows install exactly.
- **Auth is directly cross-platform-portable — the opposite of `agy`'s finding.** A scratch Docker
  volume seeded with only the host's `~/.codex/auth.json` (chatgpt OAuth mode, ~4.3KB) let a real
  `codex exec "Say PONG"` call inside the container authenticate and return a correct real model
  response with zero interactive login step. A/B tested whether `config.toml` was also needed —
  it was not; `auth.json` alone is sufficient for auth+basic exec.
- **A read-only bind-mount of the host's `~/.codex` does NOT work.** First attempt bind-mounted
  `~/.codex:/root/.codex:ro` directly — failed with `Read-only file system (os error 30)` during
  `codex`'s own app-server client init (it writes to its config dir, e.g. PATH-alias bookkeeping,
  even during a plain `exec`). Fixed by using a writable Docker volume instead, same requirement as
  `agy`'s.
- **Real end-to-end `Dispatcher.run_job` succeeded**: isolated `CADET_STATE_DIR`, real
  `cadet-codex:latest` image, real seeded `cadet-codex-auth` volume, scratch git repo → `status:
  "succeeded"`, real model response, real token-usage counts in the JSON stdout stream.
- **Real cancel-mid-flight confirmed the container is genuinely stopped, not just a DB flip**: polled
  `docker ps -a` until a real containerized job (running an actual `sleep 60` shell command via
  `skip_permissions=True`) was confirmed `Up`, then called `dispatcher.cancel()` — `docker ps -a`
  showed nothing afterward.
- **Found (not yet fixed) a fast-cancel edge case**: cancelling before the container reaches
  "Running" (~0.3s after enqueue) leaves an orphaned `Created`-state container that `--rm` never
  reaps. See "Containerized `codex` execution" above for detail — affects `agy`'s stop path too.
- **Live-tested 2026-07-27 (first post-commit smoke test of `210b4d0`, before this fix): `codex`'s
  own `-s`/sandbox flags do NOT work on Linux either** — resolves the "still unverified" question
  below, and the answer is worse than "differently," it's "not at all." A real
  `delegate_task(provider="codex", ...)` with default flags (no `skip_permissions`) came back
  dispatcher-`succeeded` but codex itself refused to run the requested git commands:
  *"the execution sandbox fails before command launch because `bubblewrap` is unavailable."*
  Root-caused empirically (not assumed): installing `bubblewrap` in the image does NOT fix it —
  built a scratch image with it installed, then ran `bwrap --unshare-all ... echo hello` under
  `--cap-drop=ALL --security-opt=no-new-privileges` (fails), under each flag alone (fails), and
  under **no restricting flags at all** (fails identically) — this Docker Desktop/WSL2 host blocks
  unprivileged user-namespace creation for every container on this daemon, not something CADET's
  own security-opt flags cause or can route around. `-s read-only` and `-s workspace-write` both
  route through this same bubblewrap-gated exec path, so neither sandbox level can execute so much
  as `git status` here. **Fix applied**: `build_docker_argv` now always runs the inner codex process
  with `--dangerously-bypass-approvals-and-sandbox` (CADET's own container — `--cap-drop=ALL`,
  `--security-opt=no-new-privileges`, resource limits, `--rm`, per-job naming — is the real security
  boundary, same reasoning as the native-Windows path's abandonment above), and recovers the
  read-only/read-write distinction via the `/workspace` bind mount itself (`:ro` when
  `skip_permissions=False` and `sandbox=True`, default `:rw` otherwise) — kernel VFS enforcement,
  not subject to the userns restriction.
- **Still unverified**: whether an expired `access_token` refreshes silently via
  `tokens.refresh_token` inside the container the same way it presumably does natively (this
  session's test used a freshly-valid token); resource-limit adequacy for a realistic job; whether
  `agy`'s own `--sandbox` flag (also enabled by default) hits the same or a different class of
  problem — not yet checked, flagged as a follow-up.

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

## Validated `copilot` CLI behavior

Confirmed empirically against the installed CLI (`@github/copilot` npm package v1.0.75, Windows),
on 2026-07-26 — real invocations against a scratch directory, not vendor docs alone:

- **Critical — on Windows, neither `copilot.cmd` NOR its `copilot.ps1` sibling is exec'd — both
  have their own, unrelated bug, and the real fix is to invoke `node.exe` + the vendor's own
  `npm-loader.js` directly.**
  - `copilot.cmd` is a `cmd.exe` batch wrapper (`... %*`), same bug class already found for
    `cursor.cmd`: exec'ing it directly routes through `cmd.exe`, which re-parses `<`/`>`/`&`/`|`/`^`
    before `%*` is substituted. Reproduced: a prompt containing CADET's own rendered-prompt
    placeholder (`<one-line summary + outcome>`) made the `.cmd` fail with `The system cannot find
    the file specified.` (`cmd.exe` treating it as an input redirection).
  - **`copilot.ps1` looked like the fix (mirroring `cursor`'s), but has its own, different bug.**
    `copilot.ps1` internally does `& "$basedir/node.exe" ".../npm-loader.js" $args` — a second,
    internal native-command invocation. When `copilot.ps1` is launched fresh via `powershell.exe
    -File` (the same non-interactive mechanism CADET/`cursor` use — not from an already-running
    interactive PowerShell session), PowerShell's own serialization of a `$args` element containing
    an embedded literal double-quote followed eventually by a space (e.g. `content="<one-line
    summary + outcome>"`) gets corrupted during that internal re-invocation, and copilot's CLI
    (commander.js) emits `error: Invalid command format. It looks like your prompt was not
    quoted...` — a real, reproducible false positive, not a `cmd.exe` redirection issue. Confirmed
    by a minimal repro: a plain `powershell.exe -File <script> -p "<value>"` correctly preserves an
    argument containing `<`/`>` end-to-end (proven with a diagnostic script that just echoes
    `$args`) — so `-File`'s own tokenizing is not at fault; the corruption is specifically inside
    `copilot.ps1`'s own internal `& node.exe ... $args` hop, which is vendor code CADET can't patch.
  - **The fix**: invoke `node.exe` and `node_modules/@github/copilot/npm-loader.js` directly,
    skipping both `.cmd` and `.ps1` (and both their bugs) entirely. `npm-loader.js` is a thin,
    stable loader (`spawnSync(realBinary, process.argv.slice(2), {stdio:"inherit"})`, no shell
    involved) that resolves whatever platform binary is currently installed under the same
    `node_modules/@github/copilot/` tree — like pointing `cursor` at its `.ps1` instead of a
    hardcoded versioned path, this still survives `npm install -g @github/copilot` version bumps.
    Confirmed via direct repro calls (`node.exe npm-loader.js -p "<the exact failing prompt>" ...`):
    both the plan-mode text case and a real file edit (content containing both `<...>` and an
    embedded `"quoted phrase"`) succeeded with byte-for-byte correct output.
  - `node.exe`'s location: a sidecar `node.exe` next to `copilot_path` if present, else
    `CADET_COPILOT_NODE_PATH` (must point at an existing file — no silent bare `"node"` PATH
    lookup, same reasoning as `CADET_AGY_PATH`). On the actual validation machine, no sidecar
    existed next to the npm global shims (`node.exe` was only at `C:\Program Files\nodejs\
    node.exe`), so the explicit env var is what matters in practice, not just a defensive
    fallback for a hypothetical.
- **`-C <cwd>` is a real, confirmed flag** — Phase 0's research had flagged cwd-handling as
  unconfirmed/absent for this provider; it exists and works exactly as expected.
- **`--allow-all-tools` is required for reliable non-interactive (`-p`) operation and is always
  passed unconditionally**, mirroring `cursor`'s unconditional `--trust`. Confirmed both ways: with
  it, `-p`/`-C`/`--mode plan` all completed promptly and correctly; without it (and without
  `--mode plan` either), a real headless invocation asking for a file write took over 20 seconds
  and never completed the edit — the vendor's own `--help` text says as much ("required for
  non-interactive mode"). Same "known non-functional combo" class as `codex`'s broken
  `workspace-write` and `cursor`'s `--force`-without-`--trust`.
- **`--mode plan` is the genuine safe/read-only lever**, mirroring `cursor`'s `--mode plan` (not
  `codex`'s `-s read-only`): a real `--mode plan --allow-all-tools` invocation returned a text
  response with zero file writes — the plan-mode agent simply has no write tool available,
  regardless of the tool-approval flag being on.
- **Unlike `codex`/`cursor`, `sandbox=False` with `skip_permissions=False` is NOT a known-broken
  combo for this provider — it behaves identically to `skip_permissions=True` (real edits apply).**
  Because `--allow-all-tools` is unconditional (the only thing that makes non-interactive mode
  function at all) and `--mode plan`'s presence/absence is the sole write gate,
  `providers/copilot.py`'s `build_argv` only appends `--mode plan` when `sandbox=True AND
  skip_permissions=False`; every other combination allows real edits. This was independently
  confirmed via two separate real file writes (one with angle-bracket content) with no `--mode`
  flag present. This is a genuine platform difference, not an oversight — `copilot` has no distinct
  "unsandboxed but still asks permission" state in non-interactive mode.
- **`--model` is always passed explicitly (default `"auto"`)**, defensively mirroring `cursor`'s
  stance — not confirmed to have `cursor`'s sticky-global-default bug, but passing explicitly costs
  nothing and avoids relying on unconfirmed default-resolution behavior.
- **`--effort`/`--reasoning-effort` is a real, confirmed flag** (choices: `none`, `minimal`, `low`,
  `medium`, `high`, `xhigh`, `max`) — unlike `cursor`'s speculative bracket-on-model-string syntax,
  `copilot` takes it as its own argv flag. **Confirmed to hard-error when combined with
  `model="auto"`**: a real call with `--model auto --effort minimal` failed with `Error: Model
  "auto" does not support reasoning effort configuration (requested: "minimal").` `providers/
  copilot.py` applies `--effort` whenever set, without validating the paired model — callers
  requesting effort must also set a real (non-`auto`) model, or the job fails with that vendor
  error, same as CADET leaving model/effort validity to the provider's own error surface elsewhere.
- **No specific named model was confirmed available on this account** — every named model tried
  (`gpt-5.1`, `claude-sonnet-4.5`, `claude-sonnet-4`, `gpt-5`, `gpt-5-mini`, `gpt-4.1`) returned
  `Error: Model "<name>" from --model flag is not available.`; only `"auto"` was confirmed working.
  This is an account/plan limitation, not a CADET bug — `CADET_COPILOT_MODEL`/`model` still passes
  whatever string is given straight through.
- **No structured quota-exhaustion signal was found** — this account never hit a real rate limit
  during validation. `providers/copilot.py`'s `parse_error` is best-effort only, same unconfirmed
  posture as `codex`'s/`cursor`'s. Two real non-quota error strings were captured above (the
  model/effort mismatch and the model-not-available error) and are covered by `parse_error`'s tests
  as confirmed non-matches.

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
7. **Quota-exhaustion wording is only empirically confirmed for `agy`.** `codex`'s, `cursor`'s, and
   `copilot`'s `parse_error` are all best-effort guesses at vendor wording, never validated against
   a real exhausted-quota failure — forcing one deliberately to test it wasn't attempted, since it
   burns real quota. Until each provider has its own confirmed reproduction (the next time one is
   naturally exhausted during real usage — capture the exact stderr then), a `null` `error_kind` on
   a failed non-agy job does **not** mean "definitely not a quota issue" — it may just mean the
   guessed regex didn't match. Check raw `stderr` via `get_task_output` rather than trusting
   `error_kind` alone for non-agy providers in the meantime.
8. **`cursor`'s "safe-but-functional" edit mode is unreachable headlessly.** Unlike `codex` (whose
   broken middle ground is a Windows-specific upstream bug that could plausibly be fixed
   upstream), `cursor`'s gap looks structural: real edits require bypassing tool-call approval
   entirely (`--force`), and there is no confirmed way to allow edits while still gating other
   risky actions without a TTY present. Revisit if a future `cursor-agent` version adds a
   headless-safe approval channel.
9. **The recorded `pid` for a containerized `agy` job is the `docker run` client's PID, not the
   container's own lifetime.** This is handled correctly today (all 3 `dispatcher.py` kill-sites
   plus `reconcile.py`'s startup loop route `agy` through `stop_container`/`docker stop`, not a bare
   PID tree-kill — see "Containerized `agy` execution" above) but is a sharp edge for anyone
   extending this pattern to another provider: a naive PID-based kill/liveness-check silently does
   nothing useful against a container.
10. **Whether `agy_permissions.py`'s curated Windows-AppContainer allow-list is still needed inside
    the container is unverified.** See "Validated `agy` container behavior" above — this and the
    other container-specific open questions there (sandbox-vs-hardening-flag interaction,
    `--add-dir` correctness, resource-limit adequacy) were blocked by the account's quota reset
    during the initial container rollout and must be confirmed with a real job before being relied
    upon, not assumed from the Windows findings.
