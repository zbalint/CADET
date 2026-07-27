import re
from datetime import datetime, timedelta

_QUOTA_PATTERN = re.compile(r"quota reached.*resets in ([0-9dhms]+)", re.IGNORECASE | re.DOTALL)
_DURATION_PATTERN = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def _parse_duration_to_seconds(duration_str: str):
    """Parse a compound NdNhNmNs duration (fields optional, e.g. '94h31m53s' has
    no 'd' component) into total seconds. Returns None if nothing matched."""
    match = _DURATION_PATTERN.fullmatch(duration_str)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_quota_exhaustion(stderr_tail: str, finished_at_iso: str, stdout_tail: str = ""):
    """Best-effort scan of a failed job's stderr (and, defensively, stdout)
    tail for a quota-exhaustion message. Returns (error_kind,
    quota_reset_at_iso) on match, else (None, None). Not finding a match is
    not an error — quota_exhausted is opportunistic enrichment, not a
    correctness requirement (see ARCHITECTURE.md). `stdout_tail` exists
    because codex's `--json` mode was found (2026-07-27) to emit its real
    quota error as a stdout event rather than to stderr -- agy's own vendor
    text is confirmed to arrive via stderr, but scanning both costs nothing
    and guards against the same class of bug resurfacing here."""
    match = _QUOTA_PATTERN.search(f"{stderr_tail or ''}\n{stdout_tail or ''}")
    if not match:
        return None, None
    seconds = _parse_duration_to_seconds(match.group(1))
    if seconds is None:
        return None, None
    finished_dt = datetime.fromisoformat(finished_at_iso)
    reset_dt = finished_dt + timedelta(seconds=seconds)
    return "quota_exhausted", reset_dt.isoformat()
