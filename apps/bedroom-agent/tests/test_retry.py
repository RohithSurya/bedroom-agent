from __future__ import annotations

from reliability.retry import RetryPolicy, retry


def test_retry_eventually_succeeds():
    calls = {"n": 0}

    @retry(RetryPolicy(max_attempts=5, base_delay_s=0.0, jitter_s=0.0))
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("nope")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3
