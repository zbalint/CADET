import sqlite3

from cadet.db.connection import get_connection

_CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id                   TEXT PRIMARY KEY,
  context_id               TEXT NOT NULL,
  label                    TEXT,
  prompt_path              TEXT NOT NULL,
  cwd                      TEXT NOT NULL,
  provider                 TEXT NOT NULL DEFAULT 'agy',
  model                    TEXT,
  effort                   TEXT,
  skip_permissions         INTEGER NOT NULL DEFAULT 0,
  skip_quota_check         INTEGER NOT NULL DEFAULT 0,
  pid                      INTEGER,
  owner_pid                INTEGER,
  server_instance_id       TEXT,
  status                   TEXT NOT NULL,
  created_at               TEXT NOT NULL,
  started_at               TEXT,
  finished_at              TEXT,
  exit_code                INTEGER,
  timeout_s                INTEGER NOT NULL,
  stdout_log_path          TEXT NOT NULL,
  stderr_log_path          TEXT NOT NULL,
  error_message            TEXT,
  error_kind               TEXT,
  quota_reset_at           TEXT,
  quota_reset_confidence   TEXT
);
"""

_CREATE_PROVIDER_STATUS_TABLE = """
CREATE TABLE IF NOT EXISTS provider_status (
  pool_key        TEXT PRIMARY KEY,
  quota_reset_at  TEXT,
  confidence      TEXT NOT NULL,
  source_job_id   TEXT,
  updated_at      TEXT NOT NULL
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


def _ensure_skip_quota_check_column(conn: sqlite3.Connection) -> None:
    """Migration for DBs created before the pre-flight quota gate existed."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "skip_quota_check" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN skip_quota_check INTEGER NOT NULL DEFAULT 0")


def _ensure_quota_reset_confidence_column(conn: sqlite3.Connection) -> None:
    """Migration for DBs created before the pre-flight quota gate existed."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "quota_reset_confidence" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN quota_reset_confidence TEXT")


def _ensure_owner_pid_column(conn: sqlite3.Connection) -> None:
    """Migration for DBs created before the owner_pid column existed."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "owner_pid" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN owner_pid INTEGER")


def _ensure_server_instance_id_column(conn: sqlite3.Connection) -> None:
    """Migration for DBs created before the server_instance_id column existed."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "server_instance_id" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN server_instance_id TEXT")


def init_db(db_path: str) -> sqlite3.Connection:
    """Create the jobs and provider_status tables (and supporting indexes) if
    they don't already exist. Returns an open connection to the caller,
    mirroring SALTMDB's init_db signature."""
    conn = get_connection(db_path)
    with conn:
        conn.execute(_CREATE_JOBS_TABLE)
        _ensure_provider_column(conn)
        _ensure_skip_quota_check_column(conn)
        _ensure_quota_reset_confidence_column(conn)
        _ensure_owner_pid_column(conn)
        _ensure_server_instance_id_column(conn)
        conn.execute(_CREATE_PROVIDER_STATUS_TABLE)
        for stmt in _CREATE_INDEXES:
            conn.execute(stmt)
    return conn
