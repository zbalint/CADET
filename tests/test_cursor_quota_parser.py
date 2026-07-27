import unittest

from cadet.process.providers.cursor import parse_error

# NOTE: this exact wording is UNCONFIRMED (see cursor.py's module docstring) —
# no real quota exhaustion was observed during empirical validation. This
# test covers the fallback parser's mechanics against a plausible shape, not
# a vendor-verified string.
PLAUSIBLE_QUOTA_STDERR = "Error: usage limit reached. Try again in 5h30m.\n"

# Real stderr text captured 2026-07-27 during live multi-provider stress-testing
# (replaying build_docker_argv's exact argv outside CADET's dispatcher against a
# real free-tier account that had genuinely exhausted its usage cap) — see
# cursor.py's module docstring. Unlike the guessed shape above, this real
# message carries no reset-time/ETA at all.
REAL_QUOTA_STDERR = (
    "ActionRequiredError: You've hit your usage limit Get Cursor Pro for more "
    "Agent usage, unlimited Tab, and more."
)


class TestParseError(unittest.TestCase):
    def test_matches_real_confirmed_wording_with_no_reset_eta(self):
        error_kind, quota_reset_at = parse_error(REAL_QUOTA_STDERR, "2026-07-26T00:00:00")
        self.assertEqual(error_kind, "quota_exhausted")
        self.assertIsNone(quota_reset_at)

    def test_matches_plausible_wording(self):
        error_kind, quota_reset_at = parse_error(PLAUSIBLE_QUOTA_STDERR, "2026-07-26T00:00:00")
        self.assertEqual(error_kind, "quota_exhausted")
        self.assertEqual(quota_reset_at, "2026-07-26T05:30:00")

    def test_no_match_returns_none_none(self):
        stderr = "Error: Exit code: 1\nsome unrelated failure"
        self.assertEqual(parse_error(stderr, "2026-07-26T00:00:00"), (None, None))

    def test_empty_stderr_returns_none_none(self):
        self.assertEqual(parse_error("", "2026-07-26T00:00:00"), (None, None))
        self.assertEqual(parse_error(None, "2026-07-26T00:00:00"), (None, None))

    def test_free_plan_named_model_error_does_not_falsely_match_as_quota(self):
        # Real stderr text captured during empirical validation (see cursor.py) —
        # a free-tier plan restriction, not quota exhaustion.
        stderr = (
            "ActionRequiredError: Named models unavailable Free plans can only "
            "use Auto. Switch to Auto or upgrade plans to continue."
        )
        self.assertEqual(parse_error(stderr, "2026-07-26T00:00:00"), (None, None))

    def test_sandbox_unavailable_error_does_not_falsely_match_as_quota(self):
        # Real stderr text captured during empirical validation (see cursor.py) —
        # --sandbox enabled hard-errors on Windows.
        stderr = (
            "Error: Sandbox mode is enabled but not available on this system. "
            "Sandbox requires macOS or Linux."
        )
        self.assertEqual(parse_error(stderr, "2026-07-26T00:00:00"), (None, None))


if __name__ == "__main__":
    unittest.main()
