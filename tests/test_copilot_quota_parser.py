import unittest

from cadet.process.providers.copilot import parse_error

# NOTE: this exact wording is UNCONFIRMED (see copilot.py's module docstring) —
# kept as a fallback shape. These tests cover the parser's mechanics against a
# plausible shape, not a vendor-verified string.
PLAUSIBLE_QUOTA_STDERR = "Error: usage limit reached. Try again in 5h30m.\n"

# Real stderr text captured 2026-07-27 during live multi-provider stress-testing
# (two separate real delegate_task calls against a genuinely exhausted free-tier
# account) — see copilot.py's module docstring. No reset-time/ETA in the message.
REAL_QUOTA_STDERR = (
    "\nYou have exceeded your monthly quota (Request ID: F8E9:3E4679:5157DBA:5AF7631:6A674FBD)\n"
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

    def test_stdout_tail_is_also_scanned(self):
        # Defensive: copilot's confirmed message arrives via stderr, but the
        # parser should not ignore stdout if it ever showed up there instead.
        error_kind, quota_reset_at = parse_error("", "2026-07-26T00:00:00", stdout_tail=REAL_QUOTA_STDERR)
        self.assertEqual(error_kind, "quota_exhausted")
        self.assertIsNone(quota_reset_at)

    def test_no_match_returns_none_none(self):
        stderr = "Error: Exit code: 1\nsome unrelated failure"
        self.assertEqual(parse_error(stderr, "2026-07-26T00:00:00"), (None, None))

    def test_empty_stderr_returns_none_none(self):
        self.assertEqual(parse_error("", "2026-07-26T00:00:00"), (None, None))
        self.assertEqual(parse_error(None, "2026-07-26T00:00:00"), (None, None))

    def test_model_effort_mismatch_error_does_not_falsely_match_as_quota(self):
        # Real stderr text captured during empirical validation (see
        # copilot.py) — model="auto" rejecting a --effort value, not quota
        # exhaustion.
        stderr = 'Error: Model "auto" does not support reasoning effort configuration (requested: "minimal").'
        self.assertEqual(parse_error(stderr, "2026-07-26T00:00:00"), (None, None))

    def test_model_not_available_error_does_not_falsely_match_as_quota(self):
        # Real stderr text captured during empirical validation (see
        # copilot.py) — an unavailable/unrecognized --model value, not quota
        # exhaustion.
        stderr = 'Error: Model "gpt-5.1" from --model flag is not available.'
        self.assertEqual(parse_error(stderr, "2026-07-26T00:00:00"), (None, None))


if __name__ == "__main__":
    unittest.main()
