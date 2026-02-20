from __future__ import annotations

import uuid


def new_correlation_id() -> str:
    return f"c_{uuid.uuid4().hex}"


def new_idempotency_key() -> str:
    return f"i_{uuid.uuid4().hex}"
