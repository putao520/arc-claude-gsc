import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if not (ROOT / "main.py").is_file() and Path("/workspace/submission/main.py").is_file():
    ROOT = Path("/workspace/submission")
SPEC = importlib.util.spec_from_file_location("arc_submission_main", ROOT / "main.py")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)


class SelfHealTests(unittest.TestCase):
    def result(self, *, rc=1, is_error=True, terminal_reason="api_error", api_error_status=None, tail=""):
        return mod.ClaudeRunResult(
            returncode=rc,
            is_error=is_error,
            terminal_reason=terminal_reason,
            subtype="error_during_execution",
            api_error_status=api_error_status,
            tail=tail,
        )

    def test_connection_reset_is_retryable(self):
        c = mod.classify_claude_failure(
            self.result(api_error_status=400, tail='API Error: 400 upstream: read: connection reset by peer')
        )
        self.assertTrue(c.retryable)
        self.assertIn("connection reset", c.reason)

    def test_plain_http_400_is_not_retryable(self):
        c = mod.classify_claude_failure(
            self.result(api_error_status=400, tail="bad request: malformed parameter")
        )
        self.assertFalse(c.retryable)

    def test_http_429_is_retryable(self):
        c = mod.classify_claude_failure(
            self.result(api_error_status=429, tail="rate limit")
        )
        self.assertTrue(c.retryable)

    def test_http_503_is_retryable(self):
        c = mod.classify_claude_failure(
            self.result(terminal_reason="", tail="HTTP 503 service unavailable")
        )
        self.assertTrue(c.retryable)

    def test_budget_exhausted_is_not_retryable(self):
        c = mod.classify_claude_failure(
            self.result(terminal_reason="budget_exhausted", tail="max budget reached")
        )
        self.assertFalse(c.retryable)
        self.assertIn("budget_exhausted", c.reason)

    def test_invalid_api_key_is_not_retryable(self):
        c = mod.classify_claude_failure(
            self.result(terminal_reason="api_error", tail="invalid api key")
        )
        self.assertFalse(c.retryable)

    def test_passed_module_is_skipped_on_resume(self):
        class Traceability:
            @staticmethod
            def get_node_state(node_id):
                return {"req_id": node_id, "state": "PASSED"}

        class Runtime:
            traceability = Traceability()

        self.assertTrue(mod.module_already_passed(Runtime(), "REQ-1"))

    def test_explicit_fallback_switches_only_when_configured(self):
        old = mod.os.environ.get("ARC_FALLBACK_BASE_URLS")
        try:
            mod.os.environ["ARC_FALLBACK_BASE_URLS"] = "https://backup.example/v1"
            urls = mod.configured_base_urls("https://api.arc-bench.com/v1")
            self.assertEqual(urls, ["https://api.arc-bench.com/v1", "https://backup.example/v1"])
            self.assertEqual(mod.base_url_for_attempt(urls, 1), "https://api.arc-bench.com/v1")
            self.assertEqual(mod.base_url_for_attempt(urls, 2), "https://backup.example/v1")
            self.assertEqual(mod.base_url_for_attempt(urls, 6), "https://backup.example/v1")
        finally:
            if old is None:
                mod.os.environ.pop("ARC_FALLBACK_BASE_URLS", None)
            else:
                mod.os.environ["ARC_FALLBACK_BASE_URLS"] = old

    def test_backoff_is_capped(self):
        values = [mod.retry_delay_seconds(i, 5, 60) for i in range(1, 7)]
        self.assertEqual(values, [5, 10, 20, 40, 60, 60])

    def test_retry_then_success(self):
        calls = []
        sleeps = []
        retry_events = []

        def run(attempt):
            calls.append(attempt)
            if attempt == 1:
                return self.result(tail="connection reset by peer")
            return self.result(rc=0, is_error=False, terminal_reason="", tail="")

        result, attempts = mod.execute_with_retry(
            run,
            max_retries=5,
            base_seconds=5,
            max_seconds=60,
            on_retry=lambda attempt, result, classification, delay: retry_events.append(
                (attempt, classification.reason, delay)
            ),
            sleep_fn=lambda seconds: sleeps.append(seconds),
        )
        self.assertEqual(attempts, 2)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(sleeps, [5])
        self.assertEqual(retry_events[0][0], 1)
        self.assertEqual(result.returncode, 0)

    def test_non_retryable_stops_immediately(self):
        calls = []
        result, attempts = mod.execute_with_retry(
            lambda attempt: calls.append(attempt) or self.result(tail="invalid api key"),
            max_retries=5,
            base_seconds=5,
            max_seconds=60,
            sleep_fn=lambda _: self.fail("must not sleep"),
        )
        self.assertEqual(attempts, 1)
        self.assertEqual(calls, [1])
        self.assertNotEqual(result.returncode, 0)

    def test_parse_terminal_result(self):
        lines = [
            json.dumps({"type": "assistant", "message": {"content": []}}),
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "terminal_reason": "api_error",
                    "subtype": "error_during_execution",
                    "api_error_status": 400,
                }
            ),
        ]
        self.assertEqual(
            mod.parse_terminal_result(lines),
            (True, "api_error", "error_during_execution", 400),
        )


if __name__ == "__main__":
    unittest.main()
