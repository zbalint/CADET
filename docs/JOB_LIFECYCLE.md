# CADET Job Lifecycle

> Status: design-only — see [ARCHITECTURE.md](./ARCHITECTURE.md) for context and
> [MCP_TOOLS.md](./MCP_TOOLS.md) for the tools that read/write this state machine.

## States

`pending` → `running` → one of `{succeeded, failed, timeout, cancelled}`, plus a
reconciliation-only terminal state `unknown-interrupted`.

```
pending ──dispatch──▶ running ──exit 0────────▶ succeeded
   │                     │  ──exit != 0────────▶ failed
   │                     │  ──timeout_s hit────▶ timeout
   │                     └──cancel_task─────────▶ cancelled
   └──cancel_task (pre-dispatch)─────────────────▶ cancelled
   (any "running" row found at startup)──────────▶ unknown-interrupted
```

- **`pending → running`**: the dispatcher acquires a concurrency slot and spawns the subprocess.
- **`pending → cancelled`**: `cancel_task` called before dispatch. The dispatcher re-checks the
  row is still `status='pending'` immediately before spawning, and skips spawning entirely if it
  finds `cancelled` instead — this is what makes pre-dispatch cancellation safe.
- **`running → succeeded` / `running → failed`**: subprocess exits; `succeeded` iff
  `exit_code == 0`. (Note: exit code is a process-level signal only — see
  [ARCHITECTURE.md](./ARCHITECTURE.md#open-questions--risks-unresolved--flagged-for-whoever-implements-next)
  on why Claude should still inspect output/SALTMDB for true task success.) Immediately before
  finalizing a `failed` row, `run_job` also does the best-effort quota-exhaustion scan described in
  [ARCHITECTURE.md](./ARCHITECTURE.md#quota-exhaustion-detection), populating `error_kind`/
  `quota_reset_at` when it matches — `quota_exhausted` is a label on `failed`, not a separate state.
- **`running → timeout`**: `asyncio.wait_for(proc.wait(), timeout=timeout_s)` raises; the process
  tree is killed (see Process Management below), reaped, then marked `timeout`.
- **`running → cancelled`**: explicit `cancel_task` call; process tree killed the same way.
- **`running → unknown-interrupted`**: startup-reconciliation only (see below) — never produced
  during normal operation.

## Race safety

Three different code paths can try to finalize the same job: normal subprocess completion, the
timeout handler, and an explicit `cancel_task` call arriving mid-run. Rather than coordinating
these with in-memory locks/events, every terminal-state write is a single conditional SQL update:

```sql
UPDATE jobs SET status = ?, exit_code = ?, finished_at = ?
WHERE job_id = ? AND status = 'running'
```

Whichever writer gets `rowcount == 1` won the race and proceeds to finalize (close log handles,
release the concurrency slot). Any writer that arrives after the row is already terminal gets
`rowcount == 0` and simply no-ops. The database is the single source of truth — no
`asyncio.Event`/lock coordination needed between the three code paths.

## Dispatcher & concurrency

- `delegate_task` only ever inserts a `pending` row and enqueues the `job_id` on an
  `asyncio.Queue` — it never awaits subprocess completion itself.
- One long-lived dispatcher task owns the queue and a `CADET_MAX_CONCURRENT`-sized semaphore:
  `while True: job_id = await queue.get(); await semaphore.acquire(); asyncio.create_task(run_job(job_id))`.
  The semaphore is released inside `run_job`'s `finally` block.
- `run_job` re-checks `status == 'pending'` immediately before actually spawning, to catch a
  `cancel_task` that raced in while the job was queued.
- Each dispatched job is its own `asyncio.create_task(run_job(job_id))` — independent of whenever
  Claude later happens to call `check_task_status`/`get_task_output`.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the job store schema and on-disk log layout this
state machine reads/writes, and [CONFIGURATION.md](./CONFIGURATION.md) for
`CADET_MAX_CONCURRENT`/timeout defaults.

## Startup reconciliation

Run once, inside the FastMCP `lifespan` hook (mirroring SALTMDB's own
`server_lifespan`/`init_db` startup pattern in `src/saltmdb/mcp/server.py`):

1. Open the SQLite DB (`CADET_STATE_DIR/state/cadet.db`).
2. **`status='pending'` rows**: no OS process ever existed for these — simply re-enqueue their
   `job_id`s onto a fresh `asyncio.Queue`. Nothing was lost; they dispatch normally.
3. **`status='running'` rows**: the previous process's `asyncio.subprocess` handle is gone and
   cannot be re-attached — you cannot portably `wait()`/reap a process you didn't spawn as a
   child, and Windows offers no cheap way to recover its eventual real exit code. For each:
   - Best-effort check whether the recorded `pid` is still alive.
   - If alive, best-effort tree-kill it (see Process Management) for a deterministic clean slate.
   - Regardless of outcome, mark the row `unknown-interrupted` with an `error_message` describing
     what reconciliation found/did, e.g. `"pid 12345 not found at restart"` or
     `"pid 12345 still alive at restart; force-killed"`.

**This is a documented limitation, not solved further**: if CADET restarts mid-job, that job's
true outcome is unrecoverable. Its logs remain on disk up to their last flush for manual
inspection via `get_task_output`'s returned log paths. PID-reuse false positives during the
liveness check are accepted as a known, low-probability edge case.

## Process management

- **Launch**:
  ```
  asyncio.create_subprocess_exec(
      agy_path, "-p", prompt_text,
      "--add-dir", job.cwd,
      "--print-timeout", f"{job.timeout_s}s",
      "--mode", "accept-edits",
      "--sandbox",                          # unless CADET_AGY_SANDBOX=false, see CONFIGURATION.md
      *(["--model", job.model] if job.model else []),
      *(["--effort", job.effort] if job.effort else []),
      *(["--dangerously-skip-permissions"] if job.skip_permissions else []),
      cwd=job.cwd, stdout=stdout_fh, stderr=stderr_fh,
      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
  )
  ```
  on Windows (`start_new_session=True` on POSIX). Using `create_subprocess_exec` (argv list, no
  shell) avoids shell quoting/escaping issues on the rendered prompt entirely. Log file handles
  are opened append-binary and closed in `run_job`'s `finally` block — no pipes are held open
  across the job's lifetime, so partial output survives a CADET crash.
  - **`--add-dir job.cwd` is mandatory, not optional** — empirically confirmed (see
    [ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-cli-behavior)) that without it, `agy` can
    silently write files into its own internal scratch directory instead of the target `cwd` while
    still reporting success. `cwd=job.cwd` alone is not sufficient.
  - **`--print-timeout` is passed alongside CADET's own `asyncio.wait_for` timeout**, not instead
    of it — `agy` enforces its own timeout internally, but CADET's external `wait_for` is the
    backstop that guarantees `run_job` always regains control and finalizes the row even if
    `agy`'s internal enforcement misbehaves.
  - **`--mode accept-edits`** because CADET exists to get delegated *execution* done — `plan` mode
    would just produce a plan without doing the work.
  - **`--sandbox`** defaults on (see `CADET_AGY_SANDBOX` in [CONFIGURATION.md](./CONFIGURATION.md))
    as a cheap, already-built containment layer appropriate for an unattended job nobody is
    watching in real time — but treat it as best-effort, not a guarantee: it's silently defeated
    entirely when `skip_permissions=True` (`agy` issue #36), and on Windows it also blocks
    routine command execution (Python, git) outright without a matching `unsandboxed(...)`
    permission grant, regardless of `skip_permissions`. See
    [ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-cli-behavior) for the full findings and
    `cadet-install-agy-permissions` (in [CONFIGURATION.md](./CONFIGURATION.md)) for the fix.
  - **`--dangerously-skip-permissions`** is opt-in per job (`skip_permissions` param on
    `delegate_task`, default `false`) — omitted by default so `agy`'s permission soft-deny
    behavior stays as a safety net; see the permission-handling note in
    [ARCHITECTURE.md](./ARCHITECTURE.md#validated-agy-cli-behavior). Setting it also disables
    `--sandbox`'s protection entirely, not just the permission prompts.
- **Timeout**: enforced via `asyncio.wait_for(proc.wait(), timeout=job.timeout_s)`; on
  `TimeoutError`, tree-kill (below), reap with `await proc.wait()`, then the conditional `UPDATE`
  sets `timeout`.
- **Cancel / tree-kill**: `agy` may spawn its own children (git, npm, node, etc.) during a
  refactor task, so `proc.terminate()` alone (which only signals the immediate `agy` process) is
  not sufficient:
  - **Windows** (primary target): `taskkill /PID <pid> /T /F`.
  - **POSIX** (documented for portability): spawn with `start_new_session=True`, then
    `os.killpg(os.getpgid(pid), SIGTERM)`, escalating to `SIGKILL` after a ~5s grace period.
