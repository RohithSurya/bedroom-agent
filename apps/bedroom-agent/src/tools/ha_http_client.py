from dataclasses import dataclass, field
from typing import Any
import requests

from contracts.ha import ToolCall, ToolResult
from core.idempotency import IdempotencyStore
from requests.adapters import HTTPAdapter


@dataclass
class HAToolClientHTTP:
    base_url: str = "http://localhost:8123"
    mode: str = "active"  # <-- ADD THIS
    timeout_s: float = 20
    idempotency: IdempotencyStore = field(default_factory=IdempotencyStore)
    session: requests.Session | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.session is None:
            self.session = requests.Session()

            # optional: better pooling
            adapter = HTTPAdapter(pool_connections=15, pool_maxsize=15, max_retries=0)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def execute(self, call: ToolCall) -> ToolResult:
        # client-side idempotency (cache SUCCESS only)
        cached = self.idempotency.get(call.idempotency_key)
        if cached is not None:
            details = dict(cached.details)
            details["cached"] = True
            details["mode"] = self.mode
            return ToolResult(ok=cached.ok, tool=cached.tool, details=details)

        # SHADOW mode: no side effects, no HTTP mutation calls
        if self.mode == "shadow":
            result = ToolResult(
                ok=True,
                tool=call.tool,
                details={
                    "shadow": True,
                    "cached": False,
                    "note": "HTTP call skipped in shadow mode",
                },
            )
            self.idempotency.put(call.idempotency_key, result)
            return result

        # ACTIVE mode: real HTTP call
        timeout = call.timeout_s if call.timeout_s is not None else self.timeout_s
        resp = requests.post(
            self._url(f"/tool/{call.tool}"),
            json={
                "correlation_id": call.correlation_id,
                "idempotency_key": call.idempotency_key,
                "args": call.args,
            },
            timeout=timeout,
        )
        data = resp.json()
        result = ToolResult(
            ok=bool(data.get("ok")),
            tool=str(data.get("tool", call.tool)),
            details=dict(data.get("details", {})),
        )

        if result.ok:
            self.idempotency.put(call.idempotency_key, result)

        return result

    def get_state(self) -> dict[str, Any]:
        # In shadow mode we don't need state verification anyway, but safe to return empty.
        if self.mode == "shadow":
            return {}
        resp = requests.get(self._url("/state"), timeout=self.timeout_s)
        return dict(resp.json().get("state", {}))

    def inject_failure(self, *, tool: str, times: int = 1, error: str = "simulated_error") -> None:
        requests.post(
            self._url("/failures/inject"),
            json={"tool": tool, "times": int(times), "error": error},
            timeout=self.timeout_s,
        )
