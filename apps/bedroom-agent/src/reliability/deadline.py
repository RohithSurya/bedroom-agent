from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Deadline:
    end_monotonic: float

    @staticmethod
    def from_now(seconds: float) -> "Deadline":
        return Deadline(time.monotonic() + float(seconds))

    def remaining(self) -> float:
        return max(0.0, self.end_monotonic - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def cap(self, seconds: float) -> float:
        """Return min(seconds, remaining)."""
        return min(float(seconds), self.remaining())
