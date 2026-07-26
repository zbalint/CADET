import os
import subprocess


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


def get_agy_settings_path() -> str:
    return os.path.expanduser(
        os.environ.get("CADET_AGY_SETTINGS_PATH", "~/.gemini/antigravity-cli/settings.json")
    )


def get_agy_docker_image() -> str:
    return os.environ.get("CADET_AGY_DOCKER_IMAGE", "cadet-agy:latest")


def get_agy_gemini_volume() -> str:
    return os.environ.get("CADET_AGY_GEMINI_VOLUME", "cadet-agy-gemini")


def get_agy_container_memory() -> str:
    return os.environ.get("CADET_AGY_CONTAINER_MEMORY", "2g")


def get_agy_container_cpus() -> str:
    return os.environ.get("CADET_AGY_CONTAINER_CPUS", "2")


def get_agy_container_pids_limit() -> int:
    return int(os.environ.get("CADET_AGY_CONTAINER_PIDS_LIMIT", "512"))


def get_agy_stop_grace_s() -> int:
    return int(os.environ.get("CADET_AGY_STOP_GRACE_S", "10"))


def resolve_agy_docker_image() -> str:
    """Resolve and validate the agy provider's containerized execution target.
    Replaces the old resolve_agy_path() (host CADET_AGY_PATH binary lookup) —
    agy now runs exclusively inside a Docker container (see
    docs/ARCHITECTURE.md's "Containerized agy execution" section), so the
    equivalent fail-fast check is "is Docker reachable and is the image
    built" rather than "does this exe file exist". Fails fast (raises) at
    server bootstrap rather than deferring to the first delegate_task call."""
    image = get_agy_docker_image()
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        raise RuntimeError("Docker CLI not found on PATH — is Docker Desktop installed?")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Docker CLI timed out — is Docker Desktop running?")
    if result.returncode != 0:
        raise RuntimeError(
            f"Docker image {image!r} not found locally. Build it: docker build -t {image} docker/agy/"
        )
    return image


# --- Provider-generic resolution -------------------------------------------
# Only "agy" is a real provider today; the functions below are already
# generalized so a later provider only needs to add its own env vars, not
# touch this dispatch shape. "agy" is special-cased to keep the legacy
# CADET_AGY_* env var names intact for backward compatibility.

def _env_prefix(provider: str) -> str:
    return f"CADET_{provider.upper()}"


def resolve_provider_path(provider: str) -> str:
    """Like resolve_agy_docker_image, but for any provider name. Always
    required — used by delegate_task's per-call validation, which turns a
    RuntimeError into a clean {"error": ...} response rather than letting it
    propagate."""
    if provider == "agy":
        return resolve_agy_docker_image()
    env_var = f"{_env_prefix(provider)}_PATH"
    path = os.environ.get(env_var)
    if not path:
        raise RuntimeError(f"{env_var} is not set. It must be the absolute path to the {provider} executable.")
    if not os.path.isfile(path):
        raise RuntimeError(f"{env_var} does not point to an existing file: {path}")
    return path


def get_provider_model(provider: str):
    if provider == "agy":
        return get_agy_model()
    return os.environ.get(f"{_env_prefix(provider)}_MODEL") or None


def get_provider_effort(provider: str):
    if provider == "agy":
        return get_agy_effort()
    return os.environ.get(f"{_env_prefix(provider)}_EFFORT") or None


def is_provider_sandbox_enabled(provider: str) -> bool:
    if provider == "agy":
        return is_agy_sandbox_enabled()
    val = os.environ.get(f"{_env_prefix(provider)}_SANDBOX", "true")
    return val.strip().lower() not in ("0", "false", "no", "off")


def clamp_timeout_s(timeout_s) -> int:
    if timeout_s is None:
        timeout_s = get_default_timeout_s()
    return min(int(timeout_s), get_max_timeout_s())


def get_web_host() -> str:
    return os.environ.get("CADET_WEB_HOST", "127.0.0.1")


def get_web_port() -> int:
    return int(os.environ.get("CADET_WEB_PORT", "8420"))


def is_web_enabled() -> bool:
    val = os.environ.get("CADET_WEB_ENABLED", "true")
    return val.strip().lower() not in ("0", "false", "no", "off")
