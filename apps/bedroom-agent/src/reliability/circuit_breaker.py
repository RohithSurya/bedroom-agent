from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout_s: float = 5.0

    _failures: int = 0
    _state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    _opened_at: float | None = None

    def _maybe_half_open(self) -> None:
        if self._state != "OPEN" or self._opened_at is None:
            return
        if (time.time() - self._opened_at) >= self.recovery_timeout_s:
            self._state = "HALF_OPEN"

    def state(self) -> str:
        self._maybe_half_open()
        return self._state

    def allow(self) -> bool:
        self._maybe_half_open()
        return self._state != "OPEN"

    def record_failure(self) -> None:
        self._maybe_half_open()
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = "OPEN"
            self._opened_at = time.time()

    def record_success(self) -> None:
        self._failures = 0
        self._state = "CLOSED"
        self._opened_at = None

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        self._maybe_half_open()
        if self._state == "OPEN":
            raise RuntimeError("circuit_open")

        try:
            out = fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            self.record_failure()
            raise
        else:
            self.record_success()
            return out
