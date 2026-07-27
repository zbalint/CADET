import datetime as dt

from cadet import config


def resolve_pool_key(provider: str, model) -> str:
    """Maps a (provider, model) pair to the actual unit of quota exhaustion.
    codex/cursor/copilot each have exactly one pool. agy has two independent
    pools (Gemini models vs Claude+GPT models), distinguishable by prefix.

    An unrecognized or missing agy model NEVER defaults into either real pool
    -- that would risk silently cross-blocking an unrelated pool. Worst case
    for an unrecognized model is its own single-model bucket (reduced
    pooling), never a false block of gemini/claude_gpt."""
    if provider != "agy":
        return provider
    if model is None:
        return "agy:model:none"
    if model.startswith("gemini-"):
        return "agy:gemini"
    if model.startswith("claude-") or model.startswith("gpt-oss"):
        return "agy:claude_gpt"
    return f"agy:model:{model}"


def _next_monthly_anchor(anchor_day: int, after: dt.datetime) -> dt.datetime:
    """First occurrence of `anchor_day`-of-month, at midnight, strictly after
    `after`. Both `after` and the returned value are naive local time,
    matching every other timestamp in this codebase (dispatcher._now_iso()) --
    copilot's confirmed reset instant is midnight *UTC*, so the result can be
    off by up to the local UTC offset. Accepted simplification: introducing
    real UTC handling for just this one estimate would be an inconsistent
    one-off against the rest of the naive-local convention, and the error is
    small relative to the ~1-month window being estimated."""
    candidate = after.replace(day=anchor_day, hour=0, minute=0, second=0, microsecond=0)
    if candidate <= after:
        year = candidate.year + (1 if candidate.month == 12 else 0)
        month = 1 if candidate.month == 12 else candidate.month + 1
        candidate = candidate.replace(year=year, month=month)
    return candidate


def estimate_reset(provider: str, finished_at_iso: str):
    """Only called when the provider's own parse_error found a confirmed
    quota_exhausted failure but no vendor-reported reset time. Returns
    (quota_reset_at_iso | None, confidence), where confidence is always
    "estimated" except for agy's defensive fallback ("unknown" -- agy's
    vendor message always carries a duration in practice, so this branch is
    only reachable if a future format change breaks that regex; there's no
    real cadence data to guess from, so it deliberately returns no estimate).

    Reset cadences below are calibrated from real vendor billing-cycle
    research (July 2026), not confirmed for any specific account:
    - codex: rolling 7-day weekly cap (rare fallback -- parse_error usually
      extracts an absolute date/time directly from the vendor message).
    - cursor: resets on the account's individual billing-cycle anniversary
      date, not the calendar month -- CADET can't know that day unless told
      via CADET_CURSOR_BILLING_ANCHOR_DAY, so it falls back to a
      conservative +30 days.
    - copilot: confirmed reset is 00:00 UTC on the 1st of the calendar
      month for every account -- CADET's local-time handling (see below)
      means the computed reset can still be off by up to its UTC offset
      hours, so this stays "estimated" rather than "confirmed" (that label
      is reserved for a real per-failure vendor timestamp). Overridable via
      CADET_COPILOT_BILLING_ANCHOR_DAY for an account on a different cadence
      (e.g. enterprise/proration); defaults to next 1st when unset.
    """
    finished = dt.datetime.fromisoformat(finished_at_iso)

    if provider == "codex":
        return (finished + dt.timedelta(days=7)).isoformat(timespec="seconds"), "estimated"

    if provider == "cursor":
        anchor = config.get_cursor_billing_anchor_day()
        if anchor:
            return _next_monthly_anchor(anchor, finished).isoformat(timespec="seconds"), "estimated"
        return (finished + dt.timedelta(days=30)).isoformat(timespec="seconds"), "estimated"

    if provider == "copilot":
        anchor = config.get_copilot_billing_anchor_day()
        if anchor:
            return _next_monthly_anchor(anchor, finished).isoformat(timespec="seconds"), "estimated"
        return _next_monthly_anchor(1, finished).isoformat(timespec="seconds"), "estimated"

    return None, "unknown"
