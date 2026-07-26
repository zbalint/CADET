import pathlib

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cadet import status as status_shaping
from cadet.db import job_store

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"


def create_app(dispatcher, db_path: str) -> FastAPI:
    """Builds the CADET web dashboard's FastAPI app. `dispatcher` must be the same
    live Dispatcher instance the MCP server's job-running tasks use — cancelling a
    *running* job depends on that instance's in-memory cancel-flag bookkeeping
    (see Dispatcher.cancel), so a different instance would silently mis-report
    cancelled running jobs as failed."""
    app = FastAPI(title="CADET Dashboard")
    app.state.dispatcher = dispatcher
    app.state.db_path = db_path

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/tasks")
    def list_tasks(status_filter: str | None = None, context_id: str | None = None, limit: int = 20):
        limit = max(1, min(int(limit), 200))
        jobs = job_store.list_jobs(
            status_filter=status_filter, context_id=context_id, limit=limit, db_path=app.state.db_path
        )
        return [status_shaping.shape_status_dict(job, db_path=app.state.db_path) for job in jobs]

    @app.get("/api/tasks/{job_id}")
    def get_task(job_id: str):
        job = job_store.get_job(job_id, db_path=app.state.db_path)
        if job is None:
            return JSONResponse({"error": f"no such job: {job_id}"}, status_code=404)
        return status_shaping.shape_status_dict(job, db_path=app.state.db_path)

    @app.get("/api/tasks/{job_id}/output")
    def get_task_output(job_id: str, tail_lines: int | None = None):
        job = job_store.get_job(job_id, db_path=app.state.db_path)
        if job is None:
            return JSONResponse({"error": f"no such job: {job_id}"}, status_code=404)

        stdout, stdout_truncated = status_shaping.read_log(job["stdout_log_path"], tail_lines)
        stderr, stderr_truncated = status_shaping.read_log(job["stderr_log_path"], tail_lines)
        shaped = status_shaping.shape_status_dict(job, db_path=app.state.db_path)

        return {
            "job_id": job_id,
            "status": job["status"],
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": job["exit_code"],
            "error_kind": job["error_kind"],
            "quota_reset_at": job["quota_reset_at"],
            "duration_s": shaped["elapsed_s"],
            "truncated": stdout_truncated or stderr_truncated,
            "stdout_log_path": job["stdout_log_path"],
            "stderr_log_path": job["stderr_log_path"],
        }

    @app.post("/api/tasks/{job_id}/cancel")
    async def cancel_task(job_id: str):
        result = await app.state.dispatcher.cancel(job_id)
        if result is None:
            return JSONResponse({"error": f"no such job: {job_id}"}, status_code=404)
        return {"job_id": job_id, **result}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
