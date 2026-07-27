from cadet.db.connection import managed_connection

TERMINAL_STATUSES = ("succeeded", "failed", "timeout", "cancelled", "unknown-interrupted")


def _row_to_dict(row):
    return dict(row) if row is not None else None


def insert_job(
    job_id, context_id, label, prompt_path, cwd, model, effort, skip_permissions,
    status, created_at, timeout_s, stdout_log_path, stderr_log_path,
    provider="agy", db_connection=None, db_path=None,
) -> None:
    with managed_connection(db_connection, db_path) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, context_id, label, prompt_path, cwd, provider, model, effort,
                    skip_permissions, status, created_at, timeout_s,
                    stdout_log_path, stderr_log_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, context_id, label, prompt_path, cwd, provider, model, effort,
                    1 if skip_permissions else 0, status, created_at, timeout_s,
                    stdout_log_path, stderr_log_path,
                ),
            )


def get_job(job_id, db_connection=None, db_path=None):
    with managed_connection(db_connection, db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row_to_dict(row)


def mark_running(job_id, pid, started_at, db_connection=None, db_path=None) -> bool:
    """Conditional UPDATE: only succeeds if the row is still 'pending' — catches a
    cancel_task that raced in while the job was queued."""
    with managed_connection(db_connection, db_path) as conn:
        with conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'running', pid = ?, started_at = ? "
                "WHERE job_id = ? AND status = 'pending'",
                (pid, started_at, job_id),
            )
            return cur.rowcount == 1


def finalize_terminal(
    job_id, status, exit_code=None, finished_at=None, error_message=None,
    error_kind=None, quota_reset_at=None, db_connection=None, db_path=None,
) -> bool:
    """The single conditional terminal-state write. Only succeeds if the row is
    still 'running' — whichever of {subprocess exit, timeout handler, cancel}
    gets rowcount==1 here won the race and owns finalization."""
    with managed_connection(db_connection, db_path) as conn:
        with conn:
            cur = conn.execute(
                "UPDATE jobs SET status = ?, exit_code = ?, finished_at = ?, "
                "error_message = ?, error_kind = ?, quota_reset_at = ? "
                "WHERE job_id = ? AND status = 'running'",
                (status, exit_code, finished_at, error_message, error_kind, quota_reset_at, job_id),
            )
            return cur.rowcount == 1


def force_fail(job_id, error_message, finished_at, db_connection=None, db_path=None) -> bool:
    """Conditional UPDATE for run_job's defensive catch-all: an unexpected
    exception can occur either before or after mark_running succeeds (e.g. the
    prompt file failing to open vs. a post-spawn bug), so this matches either
    'pending' or 'running' — unlike finalize_terminal, which is scoped strictly
    to 'running' for the subprocess-exit/timeout/cancel race contract. Still
    conditional (WHERE status IN ('pending','running')) so it never clobbers a
    row some other writer already finalized."""
    with managed_connection(db_connection, db_path) as conn:
        with conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'failed', finished_at = ?, error_message = ? "
                "WHERE job_id = ? AND status IN ('pending', 'running')",
                (finished_at, error_message, job_id),
            )
            return cur.rowcount == 1


def mark_cancelled_pending(job_id, finished_at, db_connection=None, db_path=None) -> bool:
    """Conditional UPDATE for pre-dispatch cancellation: only succeeds if the row
    is still 'pending' (i.e. the dispatcher hasn't spawned it yet)."""
    with managed_connection(db_connection, db_path) as conn:
        with conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ? "
                "WHERE job_id = ? AND status = 'pending'",
                (finished_at, job_id),
            )
            return cur.rowcount == 1


def mark_unknown_interrupted(job_id, error_message, finished_at, db_connection=None, db_path=None) -> bool:
    """Reconciliation-only: a 'running' row found at startup has no live subprocess
    handle to race against, so this unconditionally (well, WHERE status='running',
    which is always true for reconciliation's callers) marks it unknown-interrupted."""
    with managed_connection(db_connection, db_path) as conn:
        with conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'unknown-interrupted', finished_at = ?, error_message = ? "
                "WHERE job_id = ? AND status = 'running'",
                (finished_at, error_message, job_id),
            )
            return cur.rowcount == 1


def list_jobs(status_filter=None, context_id=None, provider_filter=None, limit=20, db_connection=None, db_path=None):
    query = "SELECT * FROM jobs WHERE 1=1"
    params = []
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if context_id:
        query += " AND context_id = ?"
        params.append(context_id)
    if provider_filter:
        query += " AND provider = ?"
        params.append(provider_filter)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with managed_connection(db_connection, db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def sweep_old_terminal_jobs(cutoff_iso, db_connection=None, db_path=None):
    """Delete terminal-state job rows whose finished_at predates cutoff_iso.
    Returns the deleted job_ids so the caller can remove their log directories.
    pending/running rows are never matched regardless of age (not in TERMINAL_STATUSES)."""
    placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
    with managed_connection(db_connection, db_path) as conn:
        with conn:
            rows = conn.execute(
                f"SELECT job_id FROM jobs WHERE status IN ({placeholders}) "
                f"AND finished_at IS NOT NULL AND finished_at < ?",
                (*TERMINAL_STATUSES, cutoff_iso),
            ).fetchall()
            job_ids = [r["job_id"] for r in rows]
            if job_ids:
                del_placeholders = ",".join("?" for _ in job_ids)
                conn.execute(f"DELETE FROM jobs WHERE job_id IN ({del_placeholders})", job_ids)
            return job_ids
