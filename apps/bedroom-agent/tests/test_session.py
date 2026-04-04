from __future__ import annotations

import time

from memory.session import SessionStore, Turn
from memory.sqlite_kv import SqliteKV


def test_get_or_create_generates_id(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    store = SessionStore(kv=kv)
    sid = store.get_or_create(None)
    assert sid is not None
    assert len(sid) > 0


def test_get_or_create_returns_existing(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    store = SessionStore(kv=kv)
    sid = store.get_or_create("my-session")
    assert sid == "my-session"


def test_add_and_retrieve_turns(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    store = SessionStore(kv=kv)
    sid = "sess-1"

    store.add_turn(sid, Turn(role="user", text="hello", intent=None, ts=1.0))
    store.add_turn(sid, Turn(role="agent", text="hi", intent="status", ts=2.0))
    store.add_turn(sid, Turn(role="user", text="sleep mode", intent="sleep_mode", ts=3.0))

    turns = store.recent_turns(sid)
    assert len(turns) == 3
    assert turns[0].role == "user"
    assert turns[0].text == "hello"
    assert turns[2].text == "sleep mode"


def test_recent_turns_limit(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    store = SessionStore(kv=kv)
    sid = "sess-2"

    for i in range(10):
        store.add_turn(sid, Turn(role="user", text=f"msg-{i}", intent=None, ts=float(i)))

    turns = store.recent_turns(sid, limit=3)
    assert len(turns) == 3
    # Should be the 3 most recent, in chronological order
    assert turns[0].text == "msg-7"
    assert turns[2].text == "msg-9"


def test_expire_stale(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    store = SessionStore(kv=kv, ttl_s=5.0)

    old_ts = time.time() - 10.0
    store.add_turn("old-sess", Turn(role="user", text="old", intent=None, ts=old_ts))
    store.add_turn("fresh-sess", Turn(role="user", text="new", intent=None, ts=time.time()))

    expired = store.expire_stale()
    assert expired == 1

    assert len(store.recent_turns("old-sess")) == 0
    assert len(store.recent_turns("fresh-sess")) == 1


def test_sessions_are_isolated(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    store = SessionStore(kv=kv)

    store.add_turn("a", Turn(role="user", text="alpha", intent=None, ts=1.0))
    store.add_turn("b", Turn(role="user", text="beta", intent=None, ts=2.0))

    assert len(store.recent_turns("a")) == 1
    assert len(store.recent_turns("b")) == 1
    assert store.recent_turns("a")[0].text == "alpha"
