from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any

from core.time import now_iso


@dataclass(frozen=True)
class JsonlLogger:
    log_dir: str
    tz_name: str

    def __post_init__(self) -> None:
        os.makedirs(self.log_dir, exist_ok=True)
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_path", os.path.join(self.log_dir, "events.jsonl"))

    def write(self, *, correlation_id: str, event_type: str, payload: dict[str, Any]) -> None:
        row = {
            "ts": now_iso(self.tz_name),
            "cid": correlation_id,
            "type": event_type,
            "payload": payload,
        }
        line = json.dumps(row, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")