import asyncio
import datetime as dt
import logging
import os
import shutil

from cadet import config
from cadet.db import job_store

logger = logging.getLogger(__name__)


def sweep_once(retention_days=None, db_path=None):
    """Delete terminal job rows (and their log directories) whose finished_at
    predates the retention cutoff. pending/running rows are never touched
    regardless of age — job_store.sweep_old_terminal_jobs only ever matches
    terminal statuses."""
    retention_days = retention_days if retention_days is not None else config.get_log_retention_days()
    cutoff_iso = (dt.datetime.now() - dt.timedelta(days=retention_days)).isoformat(timespec="seconds")
    deleted_job_ids = job_store.sweep_old_terminal_jobs(cutoff_iso, db_path=db_path)
    for job_id in deleted_job_ids:
        job_log_dir = os.path.join(config.get_logs_dir(), job_id)
        shutil.rmtree(job_log_dir, ignore_errors=True)
    return deleted_job_ids


async def retention_sweep_loop(interval_s: int, retention_days=None, db_path=None) -> None:
    """Periodic in-process loop — no external scheduler needed. Runs once
    immediately (matching "swept on startup + periodic" in CONFIGURATION.md),
    then every interval_s thereafter for as long as this task lives."""
    while True:
        try:
            deleted = sweep_once(retention_days=retention_days, db_path=db_path)
            if deleted:
                logger.info("Retention sweep removed %d job(s): %s", len(deleted), deleted)
        except Exception:
            logger.exception("Retention sweep iteration failed")
        await asyncio.sleep(interval_s)
