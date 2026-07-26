import os


def get_state_dir() -> str:
    state_dir = os.path.expanduser(os.environ.get("CADET_STATE_DIR", "~/.cadet"))
    os.makedirs(state_dir, exist_ok=True)
    return state_dir


def get_db_path() -> str:
    db_dir = os.path.join(get_state_dir(), "state")
    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "cadet.db")


def get_logs_dir() -> str:
    logs_dir = os.path.join(get_state_dir(), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


def get_job_log_dir(job_id: str) -> str:
    job_dir = os.path.join(get_logs_dir(), job_id)
    os.makedirs(job_dir, exist_ok=True)
    return job_dir


def get_default_cwd():
    return os.environ.get("CADET_DEFAULT_CWD") or None


def get_max_concurrent() -> int:
    return int(os.environ.get("CADET_MAX_CONCURRENT", "2"))


def get_default_timeout_s() -> int:
    return int(os.environ.get("CADET_DEFAULT_TIMEOUT_S", "1800"))


def get_max_timeout_s() -> int:
    return int(os.environ.get("CADET_MAX_TIMEOUT_S", "7200"))


def get_log_retention_days() -> int:
    return int(os.environ.get("CADET_LOG_RETENTION_DAYS", "14"))


def get_agy_model():
    return os.environ.get("CADET_AGY_MODEL") or None


def get_agy_effort():
    return os.environ.get("CADET_AGY_EFFORT") or None


def is_agy_sandbox_enabled() -> bool:
    val = os.environ.get("CADET_AGY_SANDBOX", "true")
    return val.strip().lower() not in ("0", "false", "no", "off")


def resolve_agy_path() -> str:
    """Resolve and validate CADET_AGY_PATH. Fails fast (raises) rather than deferring
    the error to the first delegate_task call, since an MCP-launched process often
    doesn't inherit the full interactive-shell PATH."""
    path = os.environ.get("CADET_AGY_PATH")
    if not path:
        raise RuntimeError(
            "CADET_AGY_PATH is not set. It must be the absolute path to the agy executable."
        )
    if not os.path.isfile(path):
        raise RuntimeError(f"CADET_AGY_PATH does not point to an existing file: {path}")
    return path


def clamp_timeout_s(timeout_s) -> int:
    if timeout_s is None:
        timeout_s = get_default_timeout_s()
    return min(int(timeout_s), get_max_timeout_s())
