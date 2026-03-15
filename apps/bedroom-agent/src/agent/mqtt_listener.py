from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable, Iterable, Optional

import paho.mqtt.client as mqtt

from memory.sqlite_kv import SqliteKV


def _parse_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "on", "open", "1", "yes"):
            return True
        if s in ("false", "off", "closed", "0", "no"):
            return False
    return None


def parse_door_open(payload: dict[str, Any]) -> Optional[bool]:
    # Your Z2M payload: contact=false => door OPEN, contact=true => door CLOSED
    if "contact" in payload:
        contact = _parse_bool(payload.get("contact"))
        if contact is None:
            return None
        return not contact
    return None


def parse_target_distance(payload: dict[str, Any]) -> Optional[float]:
    v = payload.get("target_distance")
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def parse_presence(payload: dict[str, Any]) -> Optional[bool]:
    # Your FP1E payload: presence=true/false
    if "presence" in payload:
        return _parse_bool(payload.get("presence"))
    return None


def _normalize_topics(raw_topics: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(raw_topics, str):
        candidates = raw_topics.split(",")
    else:
        candidates = raw_topics

    topics: list[str] = []
    for candidate in candidates:
        topic = str(candidate).strip()
        if topic and topic not in topics:
            topics.append(topic)
    return tuple(topics)


def _in_quiet_hours(tz_name: str, start_hhmm: str, end_hhmm: str) -> bool:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz).time()

    sh, sm = [int(x) for x in start_hhmm.split(":")]
    eh, em = [int(x) for x in end_hhmm.split(":")]
    start = datetime.now(tz).replace(hour=sh, minute=sm, second=0, microsecond=0).time()
    end = datetime.now(tz).replace(hour=eh, minute=em, second=0, microsecond=0).time()

    # Handles overnight windows (e.g., 23:30 -> 08:00)
    if start <= end:
        return start <= now <= end
    return (now >= start) or (now <= end)


@dataclass
class Z2MMqttListener:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None

    door_topics: str | tuple[str, ...] | list[str]
    presence_topic: str

    tz_name: str
    quiet_start: str
    quiet_end: str

    entry_window_s: int
    entry_cooldown_s: int
    vacancy_off_delay_s: int

    kv: SqliteKV
    connected: bool = field(default=False, init=False)

    # callback called when we detect an entry event
    on_enter: Callable[[dict[str, Any]], None]
    on_vacant: Callable[[dict[str, Any]], None]

    _client: mqtt.Client | None = None
    _vacancy_timer: threading.Timer | None = field(default=None, init=False, repr=False)
    _timer_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.door_topics = _normalize_topics(self.door_topics)

    @property
    def door_topic(self) -> str:
        return self.door_topics[0] if self.door_topics else ""

    def start(self) -> None:
        client = mqtt.Client(client_id=f"bedroom-agent-{int(time.time())}", clean_session=True)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        if self.mqtt_username:
            client.username_pw_set(self.mqtt_username, self.mqtt_password or "")

        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        client.connect(self.mqtt_host, self.mqtt_port, keepalive=30)
        client.loop_start()
        self._client = client

        self.kv.append_event(
            "mqtt_listener_started", {"host": self.mqtt_host, "port": self.mqtt_port}
        )

    def stop(self) -> None:
        self._cancel_vacancy_timer()
        if not self._client:
            return
        try:
            self._client.loop_stop()
            self._client.disconnect()
        finally:
            self.kv.append_event("mqtt_listener_stopped", {})

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: dict, rc: int) -> None:
        # Subscribe only to the topics we care about (fast + low noise)
        self.connected = rc == 0
        for topic in self.door_topics:
            client.subscribe(topic)
        client.subscribe(self.presence_topic)
        self.kv.append_event(
            "mqtt_connected",
            {
                "rc": rc,
                "door_topic": self.door_topic,
                "door_topics": list(self.door_topics),
                "presence_topic": self.presence_topic,
            },
        )

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, rc: int) -> None:
        self.connected = False
        self.kv.append_event("mqtt_disconnected", {"rc": rc})

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return

        if topic in self.door_topics:
            door_open = parse_door_open(payload)
            if door_open is None:
                return

            now = time.time()
            self.kv.set("belief", "door_open", door_open)

            if door_open:
                # Arm entry window
                self.kv.set("belief", "last_door_open_ts", now)

                # Edge case: presence is already true (mmWave stuck true) → still allow entry trigger
                presence_now = bool(self.kv.get("belief", "presence", False))
                if presence_now:
                    self._maybe_trigger_enter(now)

            self.kv.append_event("door_update", {"door_open": door_open, "topic": topic})
            return

        if topic == self.presence_topic:
            now = time.time()

            presence = parse_presence(payload)
            if presence is None:
                return

            prev_presence = bool(self.kv.get("belief", "presence", False))
            self.kv.set("belief", "presence", presence)

            dist = parse_target_distance(payload)
            if dist is not None:
                prev_dist = self.kv.get("belief", "target_distance", None)
                self.kv.set("belief", "target_distance", dist)
            else:
                prev_dist = self.kv.get("belief", "target_distance", None)

            self.kv.append_event(
                "presence_update",
                {"presence": presence, "topic": topic, "target_distance": dist},
            )

            if presence:
                self._cancel_vacancy_timer()
            else:
                self._schedule_vacancy_timer(now)

            # Primary trigger: rising edge (False -> True)
            if (not prev_presence) and presence:
                self._maybe_trigger_enter(now)
                return

            # Fallback trigger: mmWave stuck true but distance changes far->near after door opened
            if presence and dist is not None and prev_dist is not None:
                try:
                    prev_d = float(prev_dist)
                except Exception:
                    prev_d = None

                # Tune thresholds if needed
                if prev_d is not None and prev_d > 2.2 and dist <= 2.0:
                    self._maybe_trigger_enter(now)

    def _maybe_trigger_enter(self, now_ts: float) -> None:
        last_door_open_ts = float(self.kv.get("belief", "last_door_open_ts", 0.0) or 0.0)
        last_trigger_ts = float(self.kv.get("belief", "last_enter_trigger_ts", 0.0) or 0.0)

        # Cooldown
        if (now_ts - last_trigger_ts) < self.entry_cooldown_s:
            return

        # Must have door opened recently
        if last_door_open_ts <= 0:
            return
        if (now_ts - last_door_open_ts) > self.entry_window_s:
            return

        quiet = _in_quiet_hours(self.tz_name, self.quiet_start, self.quiet_end)

        # Mark trigger immediately to avoid duplicates
        self.kv.set("belief", "last_enter_trigger_ts", now_ts)
        self.kv.append_event("enter_detected", {"quiet_hours": quiet})

        # Let the app decide what brightness/action to take
        self.on_enter({"quiet_hours": quiet})

    def _schedule_vacancy_timer(self, now_ts: float) -> None:
        if self.vacancy_off_delay_s <= 0:
            self.kv.append_event("vacancy_detected", {"delay_s": 0})
            self.on_vacant({"delay_s": 0, "presence_false_ts": now_ts})
            return

        with self._timer_lock:
            if self._vacancy_timer is not None:
                self._vacancy_timer.cancel()

            timer = threading.Timer(self.vacancy_off_delay_s, self._handle_vacancy_timeout)
            timer.daemon = True
            self._vacancy_timer = timer
            timer.start()

        self.kv.set("belief", "last_presence_false_ts", now_ts)
        self.kv.append_event("vacancy_timer_started", {"delay_s": self.vacancy_off_delay_s})

    def _cancel_vacancy_timer(self) -> None:
        with self._timer_lock:
            timer = self._vacancy_timer
            self._vacancy_timer = None

        if timer is not None:
            timer.cancel()
            self.kv.append_event("vacancy_timer_cancelled", {})

    def _handle_vacancy_timeout(self) -> None:
        with self._timer_lock:
            self._vacancy_timer = None

        if bool(self.kv.get("belief", "presence", False)):
            self.kv.append_event("vacancy_timer_ignored_presence_returned", {})
            return

        last_presence_false_ts = float(self.kv.get("belief", "last_presence_false_ts", 0.0) or 0.0)
        self.kv.append_event(
            "vacancy_detected",
            {
                "delay_s": self.vacancy_off_delay_s,
                "presence_false_ts": last_presence_false_ts,
            },
        )
        self.on_vacant(
            {
                "delay_s": self.vacancy_off_delay_s,
                "presence_false_ts": last_presence_false_ts,
            }
        )
