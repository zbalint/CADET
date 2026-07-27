import asyncio
import datetime as dt
import os
import uuid

from cadet import config, status
from cadet.db import job_store
from cadet.jobs import quota_gate
from cadet.mcp.server import mcp
from cadet.process.providers import registry
from cadet.prompt.template import render_prompt

# Set once by server.py's lifespan hook after the Dispatcher is constructed.
_dispatcher = None


def set_dispatcher(dispatcher) -> None:
    global _dispatcher
    _dispatcher = dispatcher


def _require_dispatcher():
    if _dispatcher is None:
        raise RuntimeError("CADET dispatcher not initialized — server_lifespan must run first")
    return _dispatcher


def _unwrap_kwargs(kwargs: dict) -> dict:
    """FastMCP emits a required 'kwargs' schema field for bare **kwargs params; some clients
    nest their actual payload under it. Unwrap that nested dict when present, else use kwargs as-is."""
    return kwargs.get("kwargs", {}) if isinstance(kwargs.get("kwargs"), dict) else kwargs


def _resolve(explicit, kw: dict, raw_kwargs: dict, *aliases: str):
    """Resolve a parameter value: explicit arg wins, then each alias checked against
    the unwrapped kwargs dict, then against the raw kwargs dict, first alias wins within each."""
    if explicit is not None:
        return explicit
    for source in (kw, raw_kwargs):
        for alias in aliases:
            val = source.get(alias)
            if val is not None:
                return val
    return None


def _coerce_bool(val, default=False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


@mcp.tool()
async def delegate_task(
    prompt: str = None, context_id: str = None, cwd: str = None, timeout_s: int = None,
    label: str = None, provider: str = None, model: str = None, effort: str = None,
    skip_permissions: bool = None, skip_quota_check: bool = None,
    **kwargs,
) -> dict:
    """Starts a background job running the chosen provider's CLI (default: agy)
    as `<provider> -p "<rendered prompt>"`. Never blocks on the subprocess —
    returns as soon as the job is queued/dispatched."""
    kw = _unwrap_kwargs(kwargs)
    prompt_ = _resolve(prompt, kw, kwargs, "prompt")
    if not prompt_:
        return {"error": "prompt is required"}

    context_id_ = _resolve(context_id, kw, kwargs, "context_id", "project_id", "project") \
        or f"cadet-{uuid.uuid4().hex[:8]}"
    cwd_ = _resolve(cwd, kw, kwargs, "cwd") or config.get_default_cwd()
    if not cwd_ or not os.path.isdir(cwd_):
        return {"error": f"cwd is not a valid directory: {cwd_!r}"}

    provider_ = _resolve(provider, kw, kwargs, "provider") or registry.DEFAULT_PROVIDER
    if provider_ not in registry.names():
        return {"error": f"unknown provider: {provider_!r}. Supported: {', '.join(registry.names())}"}
    try:
        config.resolve_provider_path(provider_)
    except RuntimeError as exc:
        return {"error": str(exc)}
    provider_mod = registry.get(provider_)

    timeout_s_ = config.clamp_timeout_s(_resolve(timeout_s, kw, kwargs, "timeout_s"))
    label_ = _resolve(label, kw, kwargs, "label")
    model_ = _resolve(model, kw, kwargs, "model") or config.get_provider_model(provider_)
    effort_ = _resolve(effort, kw, kwargs, "effort") or config.get_provider_effort(provider_)
    skip_permissions_ = _coerce_bool(_resolve(skip_permissions, kw, kwargs, "skip_permissions"))
    skip_quota_check_ = _coerce_bool(_resolve(skip_quota_check, kw, kwargs, "skip_quota_check"))

    if not skip_quota_check_:
        # Courtesy fast-path only -- UX sugar so a caller gets an immediate,
        # synchronous answer instead of round-tripping through check_task_status.
        # NOT a substitute for the dispatcher-side gate in run_job: the pool can
        # become exhausted after this check but before a deep queue actually
        # dispatches, so that gate remains the only load-bearing enforcement.
        block = quota_gate.check_quota_gate(provider_, model_)
        if block is not None:
            return {
                "error": (
                    f"provider quota exhausted: {block['pool_key']} blocked until "
                    f"{block['quota_reset_at'] or 'unknown'} ({block['confidence']})"
                ),
                "error_kind": "quota_exhausted",
                "quota_reset_at": block["quota_reset_at"],
                "quota_reset_confidence": block["confidence"],
            }

    job_id = "job-" + uuid.uuid4().hex[:12]
    job_log_dir = config.get_job_log_dir(job_id)
    prompt_path = os.path.join(job_log_dir, "prompt.txt")
    stdout_log_path = os.path.join(job_log_dir, "stdout.log")
    stderr_log_path = os.path.join(job_log_dir, "stderr.log")

    rendered = render_prompt(
        context_id_, label_, cwd_, job_id, prompt_,
        agent_id=provider_mod.AGENT_ID, display_name=provider_mod.DISPLAY_NAME,
    )
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(rendered)

    created_at = _now_iso()
    job_store.insert_job(
        job_id=job_id, context_id=context_id_, label=label_, prompt_path=prompt_path,
        cwd=cwd_, provider=provider_, model=model_, effort=effort_, skip_permissions=skip_permissions_,
        status="pending", created_at=created_at, timeout_s=timeout_s_,
        stdout_log_path=stdout_log_path, stderr_log_path=stderr_log_path,
    )

    dispatcher = _require_dispatcher()
    await dispatcher.enqueue(job_id)
    # Yield a few times so an already-free concurrency slot has a chance to pick
    # this job up before we read back its status — best-effort, not guaranteed;
    # the row is always read fresh below rather than predicted.
    for _ in range(3):
        await asyncio.sleep(0)

    job = job_store.get_job(job_id)
    return {
        "job_id": job_id,
        "status": job["status"],
        "context_id": context_id_,
        "queue_position": status.pending_queue_position(job_id) if job["status"] == "pending" else None,
        "label": label_,
        "created_at": created_at,
    }


@mcp.tool()
def check_task_status(job_id: str = None, **kwargs) -> dict:
    """Cheap poll: a single SQLite row read, no log file I/O."""
    kw = _unwrap_kwargs(kwargs)
    job_id_ = _resolve(job_id, kw, kwargs, "job_id")
    if not job_id_:
        return {"error": "job_id is required"}
    job = job_store.get_job(job_id_)
    if job is None:
        return {"error": f"no such job: {job_id_}"}
    return status.shape_status_dict(job)


@mcp.tool()
def get_task_output(job_id: str = None, tail_lines: int = None, **kwargs) -> dict:
    """Reads log files. Safe to call on a still-running job — it peeks at
    whatever's been flushed so far."""
    kw = _unwrap_kwargs(kwargs)
    job_id_ = _resolve(job_id, kw, kwargs, "job_id")
    if not job_id_:
        return {"error": "job_id is required"}
    job = job_store.get_job(job_id_)
    if job is None:
        return {"error": f"no such job: {job_id_}"}

    tail_lines_ = _resolve(tail_lines, kw, kwargs, "tail_lines")
    stdout, stdout_truncated = status.read_log(job["stdout_log_path"], tail_lines_)
    stderr, stderr_truncated = status.read_log(job["stderr_log_path"], tail_lines_)

    shaped = status.shape_status_dict(job)
    duration_s = shaped["elapsed_s"]

    return {
        "job_id": job_id_,
        "status": job["status"],
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": job["exit_code"],
        "error_kind": job["error_kind"],
        "quota_reset_at": job["quota_reset_at"],
        "quota_reset_confidence": job["quota_reset_confidence"],
        "duration_s": duration_s,
        "truncated": stdout_truncated or stderr_truncated,
        "stdout_log_path": job["stdout_log_path"],
        "stderr_log_path": job["stderr_log_path"],
    }


@mcp.tool()
def list_tasks(
    status_filter: str = None, context_id: str = None, provider_filter: str = None,
    limit: int = None, **kwargs,
) -> list:
    kw = _unwrap_kwargs(kwargs)
    status_filter_ = _resolve(status_filter, kw, kwargs, "status_filter", "status")
    context_id_ = _resolve(context_id, kw, kwargs, "context_id", "project_id", "project")
    provider_filter_ = _resolve(provider_filter, kw, kwargs, "provider_filter", "provider")
    limit_ = _resolve(limit, kw, kwargs, "limit") or 20
    limit_ = max(1, min(int(limit_), 200))

    jobs = job_store.list_jobs(
        status_filter=status_filter_, context_id=context_id_, provider_filter=provider_filter_,
        limit=limit_,
    )
    return [status.shape_status_dict(job) for job in jobs]


@mcp.tool()
async def cancel_task(job_id: str = None, **kwargs) -> dict:
    """Idempotent — calling on an already-terminal job is a no-op that reports
    the existing terminal status rather than erroring."""
    kw = _unwrap_kwargs(kwargs)
    job_id_ = _resolve(job_id, kw, kwargs, "job_id")
    if not job_id_:
        return {"error": "job_id is required"}

    dispatcher = _require_dispatcher()
    result = await dispatcher.cancel(job_id_)
    if result is None:
        return {"error": f"no such job: {job_id_}"}
    return {"job_id": job_id_, **result}
