import unittest

from cadet.process.providers.copilot import parse_error

# NOTE: this exact wording is UNCONFIRMED (see copilot.py's module docstring) —
# no real quota exhaustion was observed during empirical validation. These
# tests cover the parser's mechanics against a plausible shape, not a
# vendor-verified string.
PLAUSIBLE_QUOTA_STDERR = "Error: usage limit reached. Try again in 5h30m.\n"


class TestParseError(unittest.TestCase):
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
