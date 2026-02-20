from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from contracts.ha import ToolResult


@dataclass
class IdempotencyStore:
    """
    Stores results by idempotency_key so repeated calls return the same result
    without re-applying side effects.
    """

    _results: Dict[str, ToolResult] = field(default_factory=dict)

    def get(self, key: str) -> Optional[ToolResult]:
        return self._results.get(key)

    def put(self, key: str, result: ToolResult) -> None:
        self._results[key] = result
