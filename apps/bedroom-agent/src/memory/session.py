from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from memory.sqlite_kv import SqliteKV


@dataclass(frozen=True)
class Turn:
    role: str  # "user" | "agent"
    text: str
    intent: str | None
    ts: float


@dataclass
class SessionStore:
    kv: SqliteKV
    ttl_s: float = 1800.0  # 30 min

    def __post_init__(self) -> None:
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self.kv._lock, self.kv._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    intent TEXT,
                    ts REAL NOT NULL,
                    PRIMARY KEY(session_id, turn_index)
                );
                """
            )

    def get_or_create(self, session_id: str | None) -> str:
        """Return the session_id. Creates a new one if None is passed."""
        if session_id:
            return session_id
        return str(uuid.uuid4())

    def add_turn(self, session_id: str, turn: Turn) -> None:
        with self.kv._lock, self.kv._conn() as c:
            row = c.execute(
                "SELECT COALESCE(MAX(turn_index), -1) FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            next_index = (row[0] if row else -1) + 1
            c.execute(
                "INSERT INTO sessions(session_id, turn_index, role, text, intent, ts) VALUES(?,?,?,?,?,?)",
                (session_id, next_index, turn.role, turn.text, turn.intent, turn.ts),
            )

    def recent_turns(self, session_id: str, limit: int = 10) -> list[Turn]:
        with self.kv._lock, self.kv._conn() as c:
            rows = c.execute(
                "SELECT role, text, intent, ts FROM sessions "
                "WHERE session_id=? ORDER BY turn_index DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        # Return in chronological order (oldest first)
        turns = [Turn(role=r[0], text=r[1], intent=r[2], ts=r[3]) for r in reversed(rows)]
        return turns

    def expire_stale(self) -> int:
        """Remove sessions that haven't been active within ttl_s. Returns count deleted."""
        cutoff = time.time() - self.ttl_s
        with self.kv._lock, self.kv._conn() as c:
            # Find sessions whose latest turn is older than cutoff
            stale = c.execute(
                "SELECT session_id FROM sessions GROUP BY session_id HAVING MAX(ts) < ?",
                (cutoff,),
            ).fetchall()
            if not stale:
                return 0
            ids = [row[0] for row in stale]
            placeholders = ",".join("?" for _ in ids)
            c.execute(f"DELETE FROM sessions WHERE session_id IN ({placeholders})", ids)
            return len(ids)
