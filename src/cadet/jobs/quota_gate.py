import datetime as dt

from cadet.db import provider_status_store
from cadet.process import quota_pool


def check_quota_gate(provider, model, db_connection=None, db_path=None):
    """Pre-flight check: is this provider's quota pool already known to be
    exhausted? Returns None if the job should proceed (nothing recorded, the
    recorded reset time has already passed, or confidence is "unknown" --
    there's no principled reset time to block on). Returns a dict describing
    the block otherwise.

    A single plain function rather than a per-provider dispatch dict -- stays
    mockable via unittest.mock.patch("cadet.jobs.dispatcher.quota_gate.check_quota_gate", ...)
    without needing the rebuilt-per-call-dict trick dispatcher.py's
    spawn_fns/parse_error_fns use (those exist because there are four
    provider-specific callables to swap; here there's only one)."""
    pool_key = quota_pool.resolve_pool_key(provider, model)
    row = provider_status_store.get_status(pool_key, db_connection=db_connection, db_path=db_path)
    if row is None or row["confidence"] == "unknown":
        return None

    if row["quota_reset_at"]:
        reset_at = dt.datetime.fromisoformat(row["quota_reset_at"])
        if reset_at <= dt.datetime.now():
            # Opportunistic self-heal -- not load-bearing, just avoids a stale
            # row lingering forever once its estimated/confirmed reset has passed.
            provider_status_store.clear_status(pool_key, db_connection=db_connection, db_path=db_path)
            return None

    return {
        "pool_key": pool_key,
        "quota_reset_at": row["quota_reset_at"],
        "confidence": row["confidence"],
    }
