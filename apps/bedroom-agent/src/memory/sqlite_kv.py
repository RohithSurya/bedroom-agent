from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import warnings


@dataclass
class SqliteKV:
    path: str

    def __post_init__(self) -> None:
        self.path = str(self._resolve_path())
        self._lock = threading.Lock()
        self._init_db()

    def _resolve_path(self) -> Path:
        configured = Path(self.path).expanduser()
        app_root = Path(__file__).resolve().parents[2]

        if not configured.is_absolute():
            configured = app_root / configured

        db_path = configured.resolve(strict=False)
        parent = db_path.parent

        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        if os.access(parent, os.W_OK):
            return db_path

        fallback_dir = app_root / ".local" / "data"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback_path = fallback_dir / db_path.name
        warnings.warn(
            f"SQLite directory '{parent}' is not writable; using '{fallback_path}' instead.",
            RuntimeWarning,
            stacklevel=2,
        )
        return fallback_path

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        return c

    def _init_db(self) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS kv (
                  namespace TEXT NOT NULL,
                  key TEXT NOT NULL,
                  value_json TEXT NOT NULL,
                  updated_at REAL NOT NULL,
                  PRIMARY KEY(namespace, key)
                );
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL NOT NULL,
                  type TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                );
                """
            )

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT value_json FROM kv WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()
            if not row:
                return default
            try:
                return json.loads(row[0])
            except Exception:
                return default

    def set(self, namespace: str, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute(
                """
                INSERT INTO kv(namespace, key, value_json, updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                  value_json=excluded.value_json,
                  updated_at=excluded.updated_at;
                """,
                (namespace, key, payload, now),
            )

    def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        now = time.time()
        p = json.dumps(payload, ensure_ascii=False)
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO events(ts, type, payload_json) VALUES(?,?,?)",
                (now, event_type, p),
            )

    def get_namespace(self, namespace: str) -> dict[str, Any]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT key, value_json FROM kv WHERE namespace=? ORDER BY updated_at DESC",
                (namespace,),
            ).fetchall()

        out: dict[str, Any] = {}
        for key, value_json in rows:
            try:
                out[key] = json.loads(value_json)
            except Exception:
                out[key] = value_json
        return out

    def recent_events(self, limit: int = 20, event_type: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT ts, type, payload_json FROM events"
        params: tuple[Any, ...] = ()
        if event_type is not None:
            sql += " WHERE type=?"
            params = (event_type,)
        sql += " ORDER BY ts DESC LIMIT ?"
        params += (int(limit),)

        with self._lock, self._conn() as c:
            rows = c.execute(sql, params).fetchall()

        out: list[dict[str, Any]] = []
        for ts, row_type, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except Exception:
                payload = {"raw": payload_json}
            out.append({"ts": float(ts), "type": row_type, "payload": payload})
        return out
