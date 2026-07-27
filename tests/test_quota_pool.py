import os
import unittest

from cadet.process import quota_pool


class TestResolvePoolKey(unittest.TestCase):
    def test_non_agy_providers_are_their_own_pool(self):
        self.assertEqual(quota_pool.resolve_pool_key("codex", None), "codex")
        self.assertEqual(quota_pool.resolve_pool_key("cursor", "whatever"), "cursor")
        self.assertEqual(quota_pool.resolve_pool_key("copilot", "whatever"), "copilot")

    def test_agy_gemini_models_map_to_gemini_pool(self):
        for model in (
            "gemini-3.6-flash-low", "gemini-3.6-flash-medium", "gemini-3.6-flash-high",
            "gemini-3.5-flash-low", "gemini-3.5-flash-medium", "gemini-3.5-flash-high",
            "gemini-3.1-pro-high", "gemini-3.1-pro-low",
        ):
            self.assertEqual(quota_pool.resolve_pool_key("agy", model), "agy:gemini")

    def test_agy_claude_and_gpt_models_map_to_claude_gpt_pool(self):
        for model in ("claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium"):
            self.assertEqual(quota_pool.resolve_pool_key("agy", model), "agy:claude_gpt")

    def test_agy_none_model_gets_its_own_bucket_not_a_real_pool(self):
        self.assertEqual(quota_pool.resolve_pool_key("agy", None), "agy:model:none")

    def test_agy_unrecognized_model_gets_its_own_bucket_not_a_real_pool(self):
        key = quota_pool.resolve_pool_key("agy", "some-future-model-9000")
        self.assertEqual(key, "agy:model:some-future-model-9000")
        self.assertNotIn(key, ("agy:gemini", "agy:claude_gpt"))


class TestEstimateReset(unittest.TestCase):
    def setUp(self):
        self._env_backup = {}
        for var in ("CADET_CURSOR_BILLING_ANCHOR_DAY", "CADET_COPILOT_BILLING_ANCHOR_DAY"):
            self._env_backup[var] = os.environ.pop(var, None)

    def tearDown(self):
        for var, val in self._env_backup.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val

    def test_codex_falls_back_to_seven_day_rolling_window(self):
        reset_at, confidence = quota_pool.estimate_reset("codex", "2026-07-27T12:00:00")
        self.assertEqual(confidence, "estimated")
        self.assertEqual(reset_at, "2026-08-03T12:00:00")

    def test_cursor_falls_back_to_thirty_days_without_anchor_day(self):
        reset_at, confidence = quota_pool.estimate_reset("cursor", "2026-07-27T12:00:00")
        self.assertEqual(confidence, "estimated")
        self.assertEqual(reset_at, "2026-08-26T12:00:00")

    def test_cursor_uses_anchor_day_when_configured(self):
        # Real-world data point: user confirmed in the Cursor app that this
        # account's quota reset date is Aug 26 -- matches CADET_CURSOR_BILLING_ANCHOR_DAY=26.
        os.environ["CADET_CURSOR_BILLING_ANCHOR_DAY"] = "26"
        reset_at, confidence = quota_pool.estimate_reset("cursor", "2026-07-27T12:00:00")
        self.assertEqual(confidence, "estimated")
        self.assertEqual(reset_at, "2026-08-26T00:00:00")

    def test_cursor_anchor_day_rolls_to_next_month_when_already_past_this_month(self):
        os.environ["CADET_CURSOR_BILLING_ANCHOR_DAY"] = "10"
        reset_at, _ = quota_pool.estimate_reset("cursor", "2026-07-27T12:00:00")
        self.assertEqual(reset_at, "2026-08-10T00:00:00")

    def test_cursor_anchor_day_same_month_when_still_upcoming(self):
        os.environ["CADET_CURSOR_BILLING_ANCHOR_DAY"] = "28"
        reset_at, _ = quota_pool.estimate_reset("cursor", "2026-07-27T12:00:00")
        self.assertEqual(reset_at, "2026-07-28T00:00:00")

    def test_copilot_falls_back_to_next_first_of_month_without_anchor_day(self):
        reset_at, confidence = quota_pool.estimate_reset("copilot", "2026-07-27T12:00:00")
        self.assertEqual(confidence, "estimated")
        self.assertEqual(reset_at, "2026-08-01T00:00:00")

    def test_copilot_uses_anchor_day_when_configured(self):
        os.environ["CADET_COPILOT_BILLING_ANCHOR_DAY"] = "15"
        reset_at, confidence = quota_pool.estimate_reset("copilot", "2026-07-27T12:00:00")
        self.assertEqual(confidence, "estimated")
        self.assertEqual(reset_at, "2026-08-15T00:00:00")

    def test_agy_defensive_fallback_returns_no_estimate(self):
        reset_at, confidence = quota_pool.estimate_reset("agy", "2026-07-27T12:00:00")
        self.assertIsNone(reset_at)
        self.assertEqual(confidence, "unknown")

    def test_december_to_january_rollover(self):
        reset_at, _ = quota_pool.estimate_reset("copilot", "2026-12-15T00:00:00")
        self.assertEqual(reset_at, "2027-01-01T00:00:00")


if __name__ == "__main__":
    unittest.main()
