from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Callable, TypeVar

T = TypeVar("T")


def run_with_timeout(seconds: float, fn: Callable[..., T], *args, **kwargs) -> T:
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=seconds)
        except FuturesTimeout as e:
            raise TimeoutError("timeout") from e
