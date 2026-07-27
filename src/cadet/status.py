import datetime as dt

from cadet.db import job_store


def pending_queue_position(job_id: str, db_path: str | None = None):
    pending = sorted(
        job_store.list_jobs(status_filter="pending", limit=10_000, db_path=db_path),
        key=lambda j: j["created_at"],
    )
    for idx, job in enumerate(pending):
        if job["job_id"] == job_id:
            return idx + 1
    return None


def shape_status_dict(job: dict, db_path: str | None = None) -> dict:
    status = job["status"]
    elapsed_s = None
    queue_position = None
    if status == "pending":
        queue_position = pending_queue_position(job["job_id"], db_path=db_path)
    elif job["started_at"] and job["finished_at"]:
        elapsed_s = (dt.datetime.fromisoformat(job["finished_at"]) - dt.datetime.fromisoformat(job["started_at"])).total_seconds()
    elif job["started_at"]:
        elapsed_s = (dt.datetime.now() - dt.datetime.fromisoformat(job["started_at"])).total_seconds()

    return {
        "job_id": job["job_id"],
        "label": job["label"],
        "context_id": job["context_id"],
        "status": status,
        "provider": job["provider"],
        "model": job["model"],
        "effort": job["effort"],
        "skip_permissions": bool(job["skip_permissions"]),
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "elapsed_s": elapsed_s,
        "exit_code": job["exit_code"],
        "error_kind": job["error_kind"],
        "quota_reset_at": job["quota_reset_at"],
        "timeout_s": job["timeout_s"],
        "queue_position": queue_position,
    }


def read_log(path, tail_lines=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return "", False
    if tail_lines is None:
        return "".join(lines), False
    if tail_lines <= 0:
        # Guard against `lines[-0:]` meaning "all lines" instead of "none" —
        # tail_lines is untrusted input from the web API's query string.
        return "", bool(lines)
    if len(lines) <= tail_lines:
        return "".join(lines), False
    return "".join(lines[-tail_lines:]), True
