import sqlite3
from contextlib import contextmanager


def get_connection(db_path: str) -> sqlite3.Connection:
    """Create a new per-request connection configured with optimized PRAGMAs."""
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA cache_size=-64000;")
    conn.execute("PRAGMA mmap_size=268435456;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def managed_connection(db_connection=None, db_path=None):
    """Context manager that acquires a connection if not provided, and closes it on exit."""
    from cadet.config import get_db_path as _get_db_path
    should_close = db_connection is None
    conn = db_connection if db_connection is not None else get_connection(db_path or _get_db_path())
    try:
        yield conn
    finally:
        if should_close:
            conn.close()
