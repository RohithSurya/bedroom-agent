from __future__ import annotations

import time
from math import ceil
from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass
class CooldownStore:
    """
    In-memory cooldown tracker (v0).
    Keyed by (cooldown_key) -> (last_allowed_epoch, cooldown_seconds)
    """

    _last_allowed: Dict[str, Tuple[float, int]] = field(default_factory=dict)

    def can_run(self, key: str, cooldown_seconds: int) -> tuple[bool, int]:
        """
        Returns (allowed, remaining_seconds).
        remaining_seconds is 0 if allowed.
        """
        now = time.time()
        last = self._last_allowed.get(key)

        if last is None:
            return True, 0

        last_ts, last_cd = last
        cd = max(cooldown_seconds, last_cd)  # if policy increases cooldown, honor the larger
        elapsed = now - last_ts
        remaining = max(0, ceil(cd - elapsed))

        if remaining > 0:
            return False, remaining
        return True, 0

    def mark_ran(self, key: str, cooldown_seconds: int) -> None:
        self._last_allowed[key] = (time.time(), cooldown_seconds)
