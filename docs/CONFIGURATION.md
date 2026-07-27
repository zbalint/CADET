# CADET Configuration

> Status: design-only — see [ARCHITECTURE.md](./ARCHITECTURE.md) for the on-disk layout these
> variables control.

Env vars only, no config file — mirrors SALTMDB's own established precedent
(`SALTMDB_DB_PATH` defaulting under `~/.saltmdb`, in `src/saltmdb/config.py`).

| Var | Default | Purpose |
|---|---|---|
| `CADET_AGY_DOCKER_IMAGE` | `cadet-agy:latest` | Docker image tag `agy` jobs run inside (see [ARCHITECTURE.md](./ARCHITECTURE.md#containerized-agy-execution)). Must already be built (`docker build -t <image> docker/agy/`) — resolved and validated (`docker image inspect`) at server startup, failing fast if Docker isn't reachable or the image doesn't exist. Replaces the old `CADET_AGY_PATH` (retired — `agy` no longer runs as a native Windows subprocess). |
| `CADET_AGY_GEMINI_VOLUME` | `cadet-agy-gemini` | Docker named volume mounted at `/root/.gemini` inside the container, holding the containerized `agy`'s own isolated auth/identity — deliberately separate from the host's interactive `~/.gemini`. See `cadet-setup-agy-docker` below for seeding it. |
| `CADET_AGY_CONTAINER_MEMORY` | `2g` | `docker run --memory` limit for `agy` containers. |
| `CADET_AGY_CONTAINER_CPUS` | `2` | `docker run --cpus` limit for `agy` containers. |
| `CADET_AGY_CONTAINER_PIDS_LIMIT` | `512` | `docker run --pids-limit` for `agy` containers. |
| `CADET_AGY_STOP_GRACE_S` | `10` | Grace period (`docker stop --timeout`) given to a container before it's force-killed, on job timeout/cancel/startup-reconciliation. |
| `CADET_DEFAULT_CWD` | none | Fallback project root used by `delegate_task` when its `cwd` param is omitted. |
| `CADET_STATE_DIR` | `~/.cadet` | Root directory for `state/cadet.db` and `logs/<job_id>/`. |
| `CADET_MAX_CONCURRENT` | `2` | Maximum concurrent `agy` subprocesses; extra jobs queue as `pending`. |
| `CADET_DEFAULT_TIMEOUT_S` | `1800` | Default per-job timeout when `delegate_task`'s `timeout_s` is omitted. |
| `CADET_MAX_TIMEOUT_S` | `7200` | Hard cap — a caller-requested `timeout_s` above this is silently clamped. |
| `CADET_LOG_RETENTION_DAYS` | `14` | Terminal jobs' log directories and DB rows older than this are swept on startup and by a periodic in-process sweep (an `asyncio.sleep` loop — no external scheduler needed). |
| `CADET_AGY_MODEL` | none (agy's own default) | Passed through as `agy --model` unless overridden per-call by `delegate_task`'s `model` param. E.g. `gemini-3.6-flash-medium`. |
| `CADET_AGY_EFFORT` | none (agy's own default) | Passed through as `agy --effort` unless overridden per-call. One of `low`\|`medium`\|`high`. |
| `CADET_AGY_SANDBOX` | `true` | Whether to pass `agy --sandbox` on every launch, run inside the container alongside the container's own isolation (see [ARCHITECTURE.md](./ARCHITECTURE.md#containerized-agy-execution)). Whether this flag still matters/behaves differently on Linux vs. the Windows findings in [ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-cli-behavior) is an open question — see [ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-container-behavior). |
| `CADET_AGY_SETTINGS_PATH` | `~/.gemini/antigravity-cli/settings.json` | Where `cadet-install-agy-permissions` reads/writes `agy`'s permission config. Override mainly for testing — `agy` itself has no flag to point at an alternate settings file, so this only affects CADET's own tooling, not what `agy` actually reads at runtime. |
| `CADET_WEB_ENABLED` | `true` | Whether to start the embedded web dashboard (see [WEB_DASHBOARD.md](./WEB_DASHBOARD.md)) alongside the MCP server. |
| `CADET_WEB_HOST` | `127.0.0.1` | Bind host for the dashboard. Loopback-only by default — there's no authentication, so only change this if you understand the exposure. |
| `CADET_WEB_PORT` | `8420` | Bind port for the dashboard. |

### `codex` provider env vars

`codex` is containerized (Phase 3, mirrors `agy`) — `CADET_CODEX_PATH` (host binary path) is
retired, replaced by Docker image resolution, same shape as `agy`'s.

| Var | Default | Purpose |
|---|---|---|
| `CADET_CODEX_DOCKER_IMAGE` | `cadet-codex:latest` | Docker image tag `codex` jobs run inside (see [ARCHITECTURE.md](./ARCHITECTURE.md#containerized-codex-execution)). Must already be built (`docker build -t <image> docker/codex/`) — resolved and validated (`docker image inspect`) at server startup. |
| `CADET_CODEX_AUTH_VOLUME` | `cadet-codex-auth` | Docker named volume mounted at `/root/.codex` inside the container, holding the containerized `codex`'s own isolated auth (`auth.json`). See `cadet-setup-codex-docker` below for seeding it — unlike `agy`, no interactive re-login step is needed afterward. |
| `CADET_CODEX_CONTAINER_MEMORY` | `2g` | `docker run --memory` limit for `codex` containers. |
| `CADET_CODEX_CONTAINER_CPUS` | `2` | `docker run --cpus` limit for `codex` containers. |
| `CADET_CODEX_CONTAINER_PIDS_LIMIT` | `512` | `docker run --pids-limit` for `codex` containers. |
| `CADET_CODEX_STOP_GRACE_S` | `10` | Grace period (`docker stop --timeout`) given to a container before it's force-killed, on job timeout/cancel/startup-reconciliation. |
| `CADET_CODEX_MODEL` | none (codex's own default) | Passed through as `codex exec -m` unless overridden per-call by `delegate_task`'s `model` param. |
| `CADET_CODEX_EFFORT` | none (codex's own default) | Passed through as `codex exec -c model_reasoning_effort=<value>` unless overridden per-call. |
| `CADET_CODEX_SANDBOX` | `true` | Whether `sandbox=True` maps to `-s read-only` (the safe default) vs `-s workspace-write`, run inside the container alongside the container's own isolation. Whether `workspace-write`'s Windows-specific breakage (missing `codex-windows-sandbox-setup.exe` helper — see [ARCHITECTURE.md](./ARCHITECTURE.md#validated-codex-cli-behavior)) still applies on Linux is unconfirmed — see [ARCHITECTURE.md](./ARCHITECTURE.md#validated-codex-container-behavior). Until confirmed, treat `codex` jobs as effectively read-only unless `skip_permissions=True` is also passed per-call (maps to `--dangerously-bypass-approvals-and-sandbox`). |

### `cursor` provider env vars

`cursor` is containerized (Phase 4, mirrors `codex`) — `CADET_CURSOR_PATH` (host binary path) is
retired, replaced by Docker image resolution. Auth is a Docker named volume (OAuth login, same
shape as `agy`'s Phase 2), not a plain API key — see `cadet-setup-cursor-docker` below.

| Var | Default | Purpose |
|---|---|---|
| `CADET_CURSOR_DOCKER_IMAGE` | `cadet-cursor:latest` | Docker image tag `cursor` jobs run inside (see [ARCHITECTURE.md](./ARCHITECTURE.md#containerized-cursor-execution)). Must already be built (`docker build -t <image> docker/cursor/`) — resolved and validated (`docker image inspect`) at server startup. |
| `CADET_CURSOR_AUTH_VOLUME` | `cadet-cursor-auth` | Docker named volume mounted at `/root/.config/cursor` inside the container, holding the containerized `cursor`'s own session credential (`auth.json`) — **not** `/root/.cursor` (the Windows install's config dir; Linux stores auth under the XDG config dir instead). See `cadet-setup-cursor-docker` below for the one-time OAuth login needed to populate it — no cross-platform credential copy from the Windows host is possible. |
| `CADET_CURSOR_API_KEY` | none (optional) | Alternative/additional auth, forwarded into the container as `CURSOR_API_KEY` (`docker run -e`) when set. Generate one from the Cursor dashboard (Integrations > User API Keys) if you'd rather not do the OAuth login. Not required — the auth volume above is the primary mechanism. |
| `CADET_CURSOR_CONTAINER_MEMORY` | `2g` | `docker run --memory` limit for `cursor` containers. |
| `CADET_CURSOR_CONTAINER_CPUS` | `2` | `docker run --cpus` limit for `cursor` containers. |
| `CADET_CURSOR_CONTAINER_PIDS_LIMIT` | `512` | `docker run --pids-limit` for `cursor` containers. |
| `CADET_CURSOR_STOP_GRACE_S` | `10` | Grace period (`docker stop --timeout`) given to a container before it's force-killed, on job timeout/cancel/startup-reconciliation. |
| `CADET_CURSOR_MODEL` | `auto` | Passed through as `agent --model` unless overridden per-call by `delegate_task`'s `model` param. **Always passed explicitly** (never omitted) — see [ARCHITECTURE.md](./ARCHITECTURE.md#validated-cursor-cli-behavior) for why omitting `--model` is unsafe (it inherits a sticky global default from `~/.cursor/cli-config.json` instead of a stateless vendor default). Free-tier accounts can only use `auto`; named models require a paid plan. |
| `CADET_CURSOR_EFFORT` | none (no effort applied) | Applied as a bracket override on the model string (`<model>[effort=<value>]`) only when a model is also set. **UNCONFIRMED** against a real call — see [ARCHITECTURE.md](./ARCHITECTURE.md#validated-cursor-cli-behavior). |
| `CADET_CURSOR_SANDBOX` | `true` | Whether `sandbox=True` maps to `--mode plan` (genuinely read-only, confirmed on both Windows and inside the Linux container via two real live calls — see [ARCHITECTURE.md](./ARCHITECTURE.md#containerized-cursor-execution)) vs. omitting `--mode` entirely. The `/workspace` bind mount's `:ro`/`:rw` distinction backs the read-only/read-write semantics regardless of what the CLI flag itself does. |

### `copilot` provider env vars

`copilot` is containerized (Phase 5, mirrors `cursor`) — `CADET_COPILOT_PATH`/
`CADET_COPILOT_NODE_PATH` (host binary + Node.js paths) are retired, replaced by Docker image
resolution. Auth is **confirmed working** (see
[ARCHITECTURE.md](./ARCHITECTURE.md#containerized-copilot-execution)) via a Docker named volume
populated by a **manually-run** `docker run -it ... copilot login` (the `cadet-setup-copilot-docker`
console script itself does not work — it lacks the real TTY the login needs), with an optional
token env var as a simpler override — see `cadet-setup-copilot-docker` below.

| Var | Default | Purpose |
|---|---|---|
| `CADET_COPILOT_DOCKER_IMAGE` | `cadet-copilot:latest` | Docker image tag `copilot` jobs run inside (see [ARCHITECTURE.md](./ARCHITECTURE.md#containerized-copilot-execution)). Must already be built (`docker build -t <image> docker/copilot/`) — resolved and validated (`docker image inspect`) at server startup. |
| `CADET_COPILOT_AUTH_VOLUME` | `cadet-copilot-auth` | Docker named volume mounted at `/root/.copilot` inside the container (the CLI's own default `COPILOT_HOME`). See `cadet-setup-copilot-docker` below — **UNCONFIRMED**, no live login has been run against it yet. |
| `CADET_COPILOT_GITHUB_TOKEN` | none (optional) | Alternative/simpler auth, forwarded into the container as `COPILOT_GITHUB_TOKEN` (`docker run -e`) when set — per real vendor `--help` docs, takes precedence over any volume-stored credential. A fine-grained PAT with the "Copilot Requests" permission, or a `gh` CLI OAuth token; classic PATs (`ghp_`) are not supported. Not required — the auth volume above is the primary mechanism, mirroring `CADET_CURSOR_API_KEY`'s relationship to `cursor`'s auth volume. |
| `CADET_COPILOT_CONTAINER_MEMORY` | `2g` | `docker run --memory` limit for `copilot` containers. |
| `CADET_COPILOT_CONTAINER_CPUS` | `2` | `docker run --cpus` limit for `copilot` containers. |
| `CADET_COPILOT_CONTAINER_PIDS_LIMIT` | `512` | `docker run --pids-limit` for `copilot` containers. |
| `CADET_COPILOT_STOP_GRACE_S` | `10` | Grace period (`docker stop --timeout`) given to a container before it's force-killed, on job timeout/cancel/startup-reconciliation. |
| `CADET_COPILOT_MODEL` | `auto` | Passed through as `copilot --model` unless overridden per-call by `delegate_task`'s `model` param. **Always passed explicitly**, defensively mirroring `cursor` (not confirmed to have the same sticky-global-default bug, but costs nothing to avoid relying on unconfirmed default behavior). |
| `CADET_COPILOT_EFFORT` | none (no effort applied) | Passed through as `copilot --effort <value>` unless overridden per-call. One of `none`\|`minimal`\|`low`\|`medium`\|`high`\|`xhigh`\|`max`. **Confirmed to hard-error when combined with `model="auto"`** (`Error: Model "auto" does not support reasoning effort configuration...`) — pair with a real `CADET_COPILOT_MODEL`/`model` if setting this. See [ARCHITECTURE.md](./ARCHITECTURE.md#validated-copilot-cli-behavior). |
| `CADET_COPILOT_SANDBOX` | `true` | Whether `sandbox=True` (with `skip_permissions=False`) maps to `--mode plan --deny-tool shell` (genuinely read-only, confirmed on native Windows — **unconfirmed inside the Linux container**) vs. omitting both flags. **Unlike `codex`/`cursor`, `sandbox=False` with `skip_permissions=False` is not a known-broken combo here** — it behaves identically to `skip_permissions=True` (real edits) because `--allow-all-tools` is always passed (required for non-interactive mode to function at all) and `--mode plan --deny-tool shell`'s presence/absence is the only actual write gate. See [ARCHITECTURE.md](./ARCHITECTURE.md#validated-copilot-cli-behavior). |

### Provider status summary

`agy`, `codex`, `cursor`, and `copilot` are all the providers CADET currently supports, and **all
four are now containerized** (Phase 2/3/4/5 respectively) — none remain on native host-binary
execution. `agy` is required/fail-fast at startup; the other three's unbuilt image just leaves that
provider out of `delegate_task`'s available providers rather than blocking server startup. A
provider with no resolvable `_DOCKER_IMAGE` simply isn't offered, and requesting it returns a clean
`{"error": ...}` rather than the server failing to start. `copilot`'s Phase 5 auth is the one
still-unconfirmed piece — see [ARCHITECTURE.md](./ARCHITECTURE.md#containerized-copilot-execution).

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

This setup step is **agy-specific, not general** — none of the other providers (Codex, Cursor,
Copilot CLI) have an equivalent persisted permission-allowlist file CADET could write into; their
permission/sandbox behavior is controlled entirely via per-invocation flags instead (see
[ARCHITECTURE.md](./ARCHITECTURE.md#provider-abstraction)). There is no
`cadet-install-<provider>-permissions` for those.

Whether this allow-list is still needed at all once `agy` runs in a container is an open question
— see [ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-container-behavior).

## Setup step: `cadet-setup-agy-docker`

A manual, one-time console script (`src/cadet/process/agy_docker_setup.py`, registered in
`pyproject.toml`) that seeds the `CADET_AGY_GEMINI_VOLUME` Docker volume from the host's
`~/.gemini`: `oauth_creds.json`, `google_accounts.json`, `installation_id`, the top-level
`settings.json`, and `antigravity-cli/settings.json` — deliberately **not** the large volatile dirs
(conversation history, cache, crashes) so the container never shares the host's interactive session
state. Always overwrites on re-run (unlike `cadet-install-agy-permissions`'s additive merge) since
these are live auth tokens that should track the host's current login.

```
cadet-setup-agy-docker          # seeds/reseeds the volume, reports what was copied
cadet-setup-agy-docker --check  # reports which files are present without writing; exit 1 if any missing
```

**This alone does not satisfy authentication.** The Linux build of `agy` uses a completely
different credential file (`antigravity-cli/antigravity-oauth-token`) than the Windows build
(`oauth_creds.json`) — confirmed empirically, no cross-platform copy is possible (see
[ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-container-behavior)). After seeding, a one-time
interactive login is required:

```
docker run --rm -it -v cadet-agy-gemini:/root/.gemini cadet-agy:latest agy -p "say OK" --print-timeout 120s
```

This prints a Google OAuth URL; visit it, approve, and paste the resulting authorization code back
into the same terminal. The result is written into the same named volume, so it persists for every
subsequent containerized job — this is a one-time step per volume, not per job.

## Setup step: `cadet-setup-codex-docker`

A manual, one-time console script (`src/cadet/process/codex_docker_setup.py`, registered in
`pyproject.toml`) that seeds the `CADET_CODEX_AUTH_VOLUME` Docker volume from the host's
`~/.codex/auth.json` — just the one file (unlike `agy`'s four), since `config.toml` was confirmed
unnecessary for auth+basic exec (see [ARCHITECTURE.md](./ARCHITECTURE.md#validated-codex-container-behavior)).
Always overwrites on re-run, same reasoning as `cadet-setup-agy-docker`.

```
cadet-setup-codex-docker          # seeds/reseeds the volume, reports what was copied
cadet-setup-codex-docker --check  # reports which files are present without writing; exit 1 if any missing
```

**Unlike `agy`, this alone IS sufficient for authentication** — codex's Windows `auth.json` is
directly portable to the Linux container, confirmed via a real successful model call with zero
interactive login step. No follow-up `docker run -it ... codex ...` login dance is needed.

## Setup step: `cadet-setup-cursor-docker`

A manual console script (`src/cadet/process/cursor_docker_setup.py`, registered in
`pyproject.toml`) that runs a one-time OAuth login against the `CADET_CURSOR_AUTH_VOLUME` Docker
volume — **more like `agy`'s two-step dance than `codex`'s single-file copy**, since there is no
host file to seed at all: cursor-agent's Linux build stores its session credential (`auth.json`)
under the XDG config dir `~/.config/cursor/`, a completely different location than the Windows
install's `~/.cursor/` (confirmed empirically via a full-home-directory capture during a real
login) — no cross-platform copy is possible.

```
cadet-setup-cursor-docker          # creates the volume if needed and runs `agent login` against it
cadet-setup-cursor-docker --check  # reports current login status via a real `agent status` call, no write
```

Running the plain (no `--check`) form prints a `cursor.com/loginDeepControl?...` URL — open it in a
browser and approve. Unlike `agy`'s documented `-it`/TTY requirement, **no TTY is actually needed
here** — confirmed empirically; the underlying `docker run --rm -v cadet-cursor-auth:/root/.config/
cursor -e NO_OPEN_BROWSER=1 cadet-cursor:latest agent login` blocks in the foreground until the
browser flow completes, then exits 0. This is a one-time step per volume, not per job — the result
persists in the named volume for every subsequent containerized `cursor` job.

## Setup step: `cadet-setup-copilot-docker`

A manual console script (`src/cadet/process/copilot_docker_setup.py`, registered in
`pyproject.toml`) that runs a one-time OAuth login against the `CADET_COPILOT_AUTH_VOLUME` Docker
volume — modeled on `cadet-setup-cursor-docker`, since there is no known portable host credential
file to seed (unlike `codex`'s single-file copy): `copilot login --help` documents that its token
is normally stored in the OS credential store on a full desktop OS, falling back to a plain-text
file under `~/.copilot/` only "if a credential store is not found."

```
cadet-setup-copilot-docker          # DOES NOT WORK for the actual login -- see below
cadet-setup-copilot-docker --check  # reports whether the volume holds any credential file at all, no write
```

**The `cadet-setup-copilot-docker` login flow (no `--check`) is CONFIRMED NOT TO WORK** — it uses a
plain (no `-it`) `subprocess.run(["docker", "run", "--rm", ...])`, which completes the OAuth
device-flow successfully but then fails with "Login succeeded, but the token was not saved. Install
a system keychain or rerun login and accept plaintext storage." (exit 1): this minimal container has
no system keychain, and the plain-text fallback needs a real allocated TTY to even offer, which a
non-interactive `subprocess.run` cannot provide (confirmed twice; piping stdin answers via `-i`
alone, no `-t`, made no difference).

**The confirmed-working setup step instead** is running this yourself in a real interactive
terminal (not through an automation harness — mirrors `agy`'s Phase 2 interactive-login
requirement):

```
docker run --rm -it -v cadet-copilot-auth:/root/.copilot cadet-copilot:latest copilot login
```

Open the printed `github.com/login/device` URL, enter the code, approve, and accept the
plaintext-storage prompt when it appears (it only appears with a real TTY present). The token then
persists in the `cadet-copilot-auth` volume for every subsequent containerized `copilot` job — this
one interactive run is a one-time step, not per-job. Verify afterward with `cadet-setup-copilot-docker
--check` (which does work, being read-only) or a real call: `docker run --rm -v
cadet-copilot-auth:/root/.copilot cadet-copilot:latest copilot -p "..." --allow-all-tools --model
auto ...`. If a token is available instead (`CADET_COPILOT_GITHUB_TOKEN`, see the env var table
above), this whole login dance can be skipped — that path needs no interactive step at all.

## Companion script: `cadet-wait-for-job`

A console script (`pyproject.toml`'s `[project.scripts]`) that blocks/polls in-process
against the job store until a given job reaches a terminal status or an internal
max-wait ceiling (default 540s, override with `--max-wait`) is hit. Not an MCP tool —
see [MCP_TOOLS.md](./MCP_TOOLS.md#companion-script-cadet-wait-for-job-not-an-mcp-tool)
for why it deliberately lives outside the 5-tool MCP surface.

```
cadet-wait-for-job <job_id> [--interval SECONDS] [--max-wait SECONDS]
```

Exit codes: `0` succeeded, `1` reached a non-success terminal status, `2` max-wait
ceiling hit (job still unresolved — re-invoke or call `check_task_status`), `3` job_id
not found (or missing argument).

Intended usage: Claude Code runs this via its own `Bash` tool with
`run_in_background: true` immediately after `delegate_task` returns a `job_id` — this
piggybacks on the harness's own Bash-background auto-notify behavior instead of
CADET building a push channel of its own. The internal max-wait ceiling is
deliberately kept below Bash's own 600s `run_in_background` timeout ceiling, since a
single invocation cannot safely promise to wait out a job's full possible
`CADET_MAX_TIMEOUT_S` (up to 7200s).

## `CADET_AGY_DOCKER_IMAGE`/`CADET_CODEX_DOCKER_IMAGE`/`CADET_CURSOR_DOCKER_IMAGE` must already be built

Unlike a host binary lookup, there's no `PATH`-resolution gotcha here — but the equivalent fail-fast
contract still applies. CADET resolves each containerized provider's image once via `docker image
inspect` (`config._resolve_docker_image`, shared by `agy`, `codex`, and `cursor`) and **fails fast
with a clear error** (not a silent no-op) if Docker isn't reachable or the image hasn't been built
(`docker build -t cadet-agy:latest docker/agy/` / `docker build -t cadet-codex:latest docker/codex/`
/ `docker build -t cadet-cursor:latest docker/cursor/`).
For `agy` this check is required/fail-fast at server startup (`__main__.py`); for `codex`/`cursor`
it's checked the same way but only blocks that one provider from being offered, not the whole
server — see [ARCHITECTURE.md](./ARCHITECTURE.md#containerized-agy-execution),
[ARCHITECTURE.md](./ARCHITECTURE.md#containerized-codex-execution), and
[ARCHITECTURE.md](./ARCHITECTURE.md#containerized-cursor-execution).

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
