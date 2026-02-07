from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.2
    max_delay_s: float = 2.0
    jitter_s: float = 0.1


def retry(policy: RetryPolicy) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapped(*args, **kwargs) -> T:
            attempt = 0
            last_err: Exception | None = None
            while attempt < policy.max_attempts:
                attempt += 1
                try:
                    return fn(*args, **kwargs)
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if attempt >= policy.max_attempts:
                        break
                    delay = min(policy.base_delay_s * (2 ** (attempt - 1)), policy.max_delay_s)
                    delay += random.uniform(0, policy.jitter_s)
                    time.sleep(delay)
            assert last_err is not None
            raise last_err

        return wrapped

    return decorator
