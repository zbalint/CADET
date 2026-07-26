import sqlite3

from cadet.db.connection import get_connection

_CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id            TEXT PRIMARY KEY,
  context_id        TEXT NOT NULL,
  label             TEXT,
  prompt_path       TEXT NOT NULL,
  cwd               TEXT NOT NULL,
  provider          TEXT NOT NULL DEFAULT 'agy',
  model             TEXT,
  effort            TEXT,
  skip_permissions  INTEGER NOT NULL DEFAULT 0,
  pid               INTEGER,
  status            TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  started_at        TEXT,
  finished_at       TEXT,
  exit_code         INTEGER,
  timeout_s         INTEGER NOT NULL,
  stdout_log_path   TEXT NOT NULL,
  stderr_log_path   TEXT NOT NULL,
  error_message     TEXT,
  error_kind        TEXT,
  quota_reset_at    TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_context_id ON jobs(context_id);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_provider ON jobs(provider);",
]


def _ensure_provider_column(conn: sqlite3.Connection) -> None:
    """Migration for DBs created before the provider column existed.
    ALTER TABLE ... DEFAULT 'agy' backfills every existing row in one
    statement — no manual UPDATE pass needed."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "provider" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN provider TEXT NOT NULL DEFAULT 'agy'")


def init_db(db_path: str) -> sqlite3.Connection:
    """Create the jobs table (and supporting indexes) if they don't already exist.
    Returns an open connection to the caller, mirroring SALTMDB's init_db signature."""
    conn = get_connection(db_path)
    with conn:
        conn.execute(_CREATE_JOBS_TABLE)
        _ensure_provider_column(conn)
        for stmt in _CREATE_INDEXES:
            conn.execute(stmt)
    return conn
