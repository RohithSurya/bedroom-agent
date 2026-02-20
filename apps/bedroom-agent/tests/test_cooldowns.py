from __future__ import annotations

import time

from core.cooldowns import CooldownStore


def test_cooldown_blocks_then_allows():
    cd = CooldownStore()
    key = "k"

    ok, rem = cd.can_run(key, 1)
    assert ok and rem == 0

    cd.mark_ran(key, 1)
    ok, rem = cd.can_run(key, 1)
    assert (not ok) and rem >= 0

    time.sleep(1.05)
    ok, rem = cd.can_run(key, 1)
    assert ok and rem == 0
