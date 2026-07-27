import unittest

from cadet.process.providers.codex import parse_error

# NOTE: this exact wording is UNCONFIRMED (see codex.py's module docstring) —
# kept as a fallback shape. These tests cover the parser's mechanics against a
# plausible shape, not a vendor-verified string.
PLAUSIBLE_QUOTA_STDERR = "Error: usage limit reached. Try again in 5h30m.\n"

# Real event captured 2026-07-27 during live multi-provider stress-testing (a
# real delegate_task call against a genuinely exhausted account) — see
# codex.py's module docstring. Critically, this arrived on STDOUT as a
# `--json` event-stream line, not on stderr (stderr only ever had the benign
# "Reading additional input from stdin..." line for this same job).
REAL_QUOTA_STDOUT = (
    '{"type": "error", "message": "You\'ve hit your usage limit. Upgrade to Plus '
    'to continue using Codex (https://chatgpt.com/explore/plus), or try again at '
    'Aug 25th, 2026 4:25 PM."}\n'
)
BENIGN_STDIN_STDERR = "Reading additional input from stdin...\n"


class TestParseError(unittest.TestCase):
    def test_matches_real_confirmed_wording_on_stdout_with_absolute_reset(self):
        # The whole point of this bug: a stderr-only scan would never see this.
        error_kind, quota_reset_at = parse_error(
            BENIGN_STDIN_STDERR, "2026-07-26T00:00:00", stdout_tail=REAL_QUOTA_STDOUT
        )
        self.assertEqual(error_kind, "quota_exhausted")
        self.assertEqual(quota_reset_at, "2026-08-25T16:25:00")

    def test_stderr_only_scan_misses_the_real_message_without_stdout_tail(self):
        # Documents the pre-fix bug shape: omitting stdout_tail entirely (old
        # call signature) must not accidentally still find it in stderr.
        self.assertEqual(
            parse_error(BENIGN_STDIN_STDERR, "2026-07-26T00:00:00"), (None, None)
        )

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

    def test_sandbox_helper_failure_does_not_falsely_match_as_quota(self):
        # Real stderr text captured during empirical validation (see codex.py).
        stderr = (
            "windows sandbox: orchestrator_helper_launch_failed: setup refresh "
            "failed to launch helper: helper=codex-windows-sandbox-setup.exe, "
            "error=program not found"
        )
        self.assertEqual(parse_error(stderr, "2026-07-26T00:00:00"), (None, None))


if __name__ == "__main__":
    unittest.main()
