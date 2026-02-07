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

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        self._maybe_half_open()

        if self._state == "OPEN":
            raise RuntimeError("circuit_open")

        try:
            out = fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = "OPEN"
                self._opened_at = time.time()
            raise
        else:
            # success
            self._failures = 0
            self._state = "CLOSED"
            self._opened_at = None
            return out
