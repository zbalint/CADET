import unittest

from cadet.process.quota import parse_quota_exhaustion

# The exact reproduced failure text from ARCHITECTURE.md's "Validated agy CLI behavior" section.
REAL_QUOTA_STDERR = (
    "Error: Individual quota reached. Please upgrade your subscription to "
    "increase your limits. Resets in 94h31m53s.\n"
)


class TestParseQuotaExhaustion(unittest.TestCase):
    def test_matches_real_vendor_string(self):
        error_kind, quota_reset_at = parse_quota_exhaustion(REAL_QUOTA_STDERR, "2026-07-26T00:00:00")
        self.assertEqual(error_kind, "quota_exhausted")
        # 94h31m53s after 2026-07-26T00:00:00
        self.assertEqual(quota_reset_at, "2026-07-29T22:31:53")

    def test_case_insensitive_match(self):
        stderr = "QUOTA REACHED. RESETS IN 5m."
        error_kind, quota_reset_at = parse_quota_exhaustion(stderr, "2026-07-26T00:00:00")
        self.assertEqual(error_kind, "quota_exhausted")
        self.assertEqual(quota_reset_at, "2026-07-26T00:05:00")

    def test_no_match_returns_none_none(self):
        stderr = "Traceback (most recent call last): ...\nSomeOtherError: unrelated failure"
        self.assertEqual(parse_quota_exhaustion(stderr, "2026-07-26T00:00:00"), (None, None))

    def test_empty_stderr_returns_none_none(self):
        self.assertEqual(parse_quota_exhaustion("", "2026-07-26T00:00:00"), (None, None))
        self.assertEqual(parse_quota_exhaustion(None, "2026-07-26T00:00:00"), (None, None))

    def test_duration_with_only_seconds(self):
        stderr = "quota reached, resets in 45s"
        _, quota_reset_at = parse_quota_exhaustion(stderr, "2026-07-26T00:00:00")
        self.assertEqual(quota_reset_at, "2026-07-26T00:00:45")

    def test_duration_with_days(self):
        stderr = "quota reached, resets in 2d3h"
        _, quota_reset_at = parse_quota_exhaustion(stderr, "2026-07-26T00:00:00")
        self.assertEqual(quota_reset_at, "2026-07-28T03:00:00")


if __name__ == "__main__":
    unittest.main()
