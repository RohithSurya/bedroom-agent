from __future__ import annotations

import pytest

from reliability.circuit_breaker import CircuitBreaker


def test_breaker_opens_after_threshold():
    br = CircuitBreaker(failure_threshold=2, recovery_timeout_s=999)

    def fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        br.call(fail)
    with pytest.raises(RuntimeError):
        br.call(fail)

    # now open
    with pytest.raises(RuntimeError, match="circuit_open"):
        br.call(lambda: "ok")



