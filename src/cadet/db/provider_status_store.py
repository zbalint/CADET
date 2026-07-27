from cadet.db.connection import managed_connection


def _row_to_dict(row):
    return dict(row) if row is not None else None


def upsert_exhaustion(
    pool_key, quota_reset_at, confidence, source_job_id, updated_at,
    db_connection=None, db_path=None,
) -> None:
    """Last-write-wins cache of the most recent known exhaustion state for a
    quota pool. Not a ledger -- concurrent writers racing on the same pool_key
    both derive from the same real event, so plain UPSERT is fine here (unlike
    every `jobs`-table write, which is a conditional UPDATE keyed on status)."""
    with managed_connection(db_connection, db_path) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO provider_status (pool_key, quota_reset_at, confidence, source_job_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(pool_key) DO UPDATE SET
                    quota_reset_at = excluded.quota_reset_at,
                    confidence = excluded.confidence,
                    source_job_id = excluded.source_job_id,
                    updated_at = excluded.updated_at
                """,
                (pool_key, quota_reset_at, confidence, source_job_id, updated_at),
            )


def get_status(pool_key, db_connection=None, db_path=None):
    with managed_connection(db_connection, db_path) as conn:
        row = conn.execute(
            "SELECT * FROM provider_status WHERE pool_key = ?", (pool_key,)
        ).fetchone()
        return _row_to_dict(row)


def clear_status(pool_key, db_connection=None, db_path=None) -> None:
    """Opportunistic self-heal (gate clears an expired row) and a future manual-clear path."""
    with managed_connection(db_connection, db_path) as conn:
        with conn:
            conn.execute("DELETE FROM provider_status WHERE pool_key = ?", (pool_key,))
