"""Console entry point (`cadet-wait-for-job`) — blocking poll wrapper around the job
store, meant to be launched by Claude Code itself via Bash(run_in_background=True)
immediately after delegate_task returns a job_id. See docs/MCP_TOOLS.md and
docs/CONFIGURATION.md for the full rationale (delegate_task never blocks; this
piggybacks on the harness's own Bash-background auto-notify instead of CADET
building a push-notification channel of its own).

Bash's run_in_background mode has a hard ceiling around 600s (10 min); CADET jobs
may legitimately run up to CADET_MAX_TIMEOUT_S (default 7200s). So this script's own
max-wait ceiling defaults to something safely under Bash's limit and exits distinctly
(code 2) rather than hanging or raising when hit — the caller re-invokes or falls
back to a manual check_task_status call.

Exit codes: 0 succeeded, 1 terminal-but-not-succeeded, 2 max-wait ceiling hit
(unresolved), 3 job_id not found / usage error.
"""
import sys
import time

from cadet import status
from cadet.db import job_store

DEFAULT_POLL_INTERVAL_S = 3
DEFAULT_MAX_WAIT_S = 540  # safely under Bash run_in_background's ~600s ceiling

EXIT_SUCCEEDED = 0
EXIT_TERMINAL_NON_SUCCESS = 1
EXIT_MAX_WAIT_CEILING_HIT = 2
EXIT_JOB_NOT_FOUND = 3


def wait_for_terminal(
    job_id, db_path=None,
    poll_interval_s=DEFAULT_POLL_INTERVAL_S, max_wait_s=DEFAULT_MAX_WAIT_S,
    sleep_fn=time.sleep, time_fn=time.monotonic,
) -> dict:
    """Polls job_store.get_job(job_id) until terminal or max_wait_s elapses.
    Returns {"outcome": "not_found"|"succeeded"|"terminal_non_success"|"ceiling_hit",
    "job": <dict or None>}. sleep_fn/time_fn are injectable so tests drive the loop
    deterministically without real waiting."""
    job = job_store.get_job(job_id, db_path=db_path)
    if job is None:
        return {"outcome": "not_found", "job": None}

    start = time_fn()
    while job["status"] not in job_store.TERMINAL_STATUSES:
        if time_fn() - start >= max_wait_s:
            return {"outcome": "ceiling_hit", "job": job}
        sleep_fn(poll_interval_s)
        job = job_store.get_job(job_id, db_path=db_path)
        if job is None:
            return {"outcome": "not_found", "job": None}  # row swept mid-wait

    if job["status"] == "succeeded":
        return {"outcome": "succeeded", "job": job}
    return {"outcome": "terminal_non_success", "job": job}


def main() -> None:
    """cadet-wait-for-job <job_id> [--interval SECONDS] [--max-wait SECONDS]"""
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print("Usage: cadet-wait-for-job <job_id> [--interval SECONDS] [--max-wait SECONDS]", file=sys.stderr)
        sys.exit(EXIT_JOB_NOT_FOUND)
    job_id = positional[0]

    poll_interval_s = DEFAULT_POLL_INTERVAL_S
    max_wait_s = DEFAULT_MAX_WAIT_S
    if "--interval" in args:
        poll_interval_s = float(args[args.index("--interval") + 1])
    if "--max-wait" in args:
        max_wait_s = float(args[args.index("--max-wait") + 1])

    result = wait_for_terminal(job_id, poll_interval_s=poll_interval_s, max_wait_s=max_wait_s)
    outcome, job = result["outcome"], result["job"]

    if outcome == "not_found":
        print(f"cadet-wait-for-job: no such job: {job_id}")
        sys.exit(EXIT_JOB_NOT_FOUND)

    shaped = status.shape_status_dict(job)

    if outcome == "ceiling_hit":
        print(f"cadet-wait-for-job: max-wait ceiling ({max_wait_s}s) hit before job {job_id} reached a terminal status.")
        print(f"  current status: {shaped['status']}")
        print("  re-invoke cadet-wait-for-job to keep waiting, or call check_task_status directly.")
        sys.exit(EXIT_MAX_WAIT_CEILING_HIT)

    print(f"cadet-wait-for-job: job {job_id} reached terminal status: {shaped['status']}")
    print(f"  label: {shaped['label']}")
    print(f"  context_id: {shaped['context_id']}")
    print(f"  elapsed_s: {shaped['elapsed_s']}")
    print(f"  exit_code: {shaped['exit_code']}")
    if shaped["error_kind"]:
        print(f"  error_kind: {shaped['error_kind']}")
        print(f"  quota_reset_at: {shaped['quota_reset_at']}")

    sys.exit(EXIT_SUCCEEDED if outcome == "succeeded" else EXIT_TERMINAL_NON_SUCCESS)


if __name__ == "__main__":
    main()
