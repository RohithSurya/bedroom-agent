from __future__ import annotations

import json
import subprocess

from vision.image_source import BedroomImageSource
from vision.room_analyzer import BedroomRoomAnalyzer
from memory.sqlite_kv import SqliteKV


class FakeLLM:
    def _parse_json_response(self, raw: str):
        return json.loads(raw)

    def generate_raw(self, *, prompt, images_b64=None, temperature=0.2, num_predict=None):
        return {
            "response": (
                '{"occupied": false, "bed_state": "partial", "desk_state": "active", '
                '"focus_readiness": 0.58, "sleep_readiness": 0.31, '
                '"issues": ["desk surface clutter", "bright lighting"], '
                '"query_answer": "There is no visible water bottle on the bed.", '
                '"summary": "The room looks moderately cluttered and not ideal for focused work."}'
            ),
            "done_reason": "stop",
        }

    def generate_json(
        self, *, prompt, schema=None, images_b64=None, temperature=0.2, num_predict=None
    ):
        assert images_b64
        return {
            "occupied": False,
            "bed_state": "partial",
            "desk_state": "active workspace with items arranged",
            "focus_readiness": 0.58,
            "sleep_readiness": 0.31,
            "issues": ["desk surface clutter", "bright lighting"],
            "query_answer": "There is no visible water bottle on the bed.",
            "summary": "The room looks moderately cluttered and not ideal for focused work.",
        }


def test_room_analyzer_uses_fallback_file_and_persists_analysis(tmp_path):
    image_path = tmp_path / "bedroom.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    source = BedroomImageSource(
        base_url="http://localhost:8123",
        token="",
        camera_mode="file",
        camera_entity_id="",
        camera_device="/dev/video0",
        camera_width=640,
        camera_height=480,
        camera_skip_frames=30,
        fallback_image_path=str(image_path),
    )
    analyzer = BedroomRoomAnalyzer(
        kv=kv,
        llm=FakeLLM(),
        image_source=source,
        enabled=True,
        prompt_profile="general",
    )

    out = analyzer.analyze("Analyze my room")

    assert "desk looks actively in use" in out["summary"].lower()
    assert "bed is partially covered" in out["summary"].lower()
    assert out["structured"]["focus_readiness"] == 0.4
    assert out["structured"]["sleep_readiness"] == 0.45
    stored = kv.get("vision", "latest_bedroom_analysis")
    assert stored["source"] == "file"
    assert stored["desk_state"] == "active"


def test_room_analyzer_fuses_presence_belief_for_occupied(tmp_path):
    image_path = tmp_path / "bedroom.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.set("belief", "presence", True)
    source = BedroomImageSource(
        base_url="http://localhost:8123",
        token="",
        camera_mode="file",
        camera_entity_id="",
        camera_device="/dev/video0",
        camera_width=640,
        camera_height=480,
        camera_skip_frames=30,
        fallback_image_path=str(image_path),
    )
    analyzer = BedroomRoomAnalyzer(
        kv=kv,
        llm=FakeLLM(),
        image_source=source,
        enabled=True,
        prompt_profile="general",
    )

    out = analyzer.analyze("Analyze my room")

    assert out["structured"]["occupied"] is True
    assert out["summary"].startswith("Room appears occupied.")


def test_room_analyzer_uses_query_answer_for_specific_question(tmp_path):
    image_path = tmp_path / "bedroom.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    source = BedroomImageSource(
        base_url="http://localhost:8123",
        token="",
        camera_mode="file",
        camera_entity_id="",
        camera_device="/dev/video0",
        camera_width=640,
        camera_height=480,
        camera_skip_frames=30,
        fallback_image_path=str(image_path),
    )
    analyzer = BedroomRoomAnalyzer(
        kv=kv,
        llm=FakeLLM(),
        image_source=source,
        enabled=True,
        prompt_profile="general",
    )

    out = analyzer.analyze("Any water bottle on my bed?")

    assert out["summary"] == "There is no visible water bottle on the bed."


class LooseFakeLLM(FakeLLM):
    def generate_raw(self, *, prompt, images_b64=None, temperature=0.2, num_predict=None):
        return {
            "response": (
                '{"query_answer":"No, there is no person visible on the bed.",'
                '"summary":"Room shows a tidy workspace with an active desk and partially made bed, indicating focus readiness.",'
                '"bed_state":"partially made",'
                '"desk_state":"active",'
                '"focus_readiness":0.8,'
                '"sleep_readiness":0.2,'
                '"issues":["Laundry basket near desk","Minimal personal items on bed"]}'
            ),
            "done_reason": "stop",
        }


def test_room_analyzer_coerces_loose_model_output(tmp_path):
    image_path = tmp_path / "bedroom.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    source = BedroomImageSource(
        base_url="http://localhost:8123",
        token="",
        camera_mode="file",
        camera_entity_id="",
        camera_device="/dev/video0",
        camera_width=640,
        camera_height=480,
        camera_skip_frames=30,
        fallback_image_path=str(image_path),
    )
    analyzer = BedroomRoomAnalyzer(
        kv=kv,
        llm=LooseFakeLLM(),
        image_source=source,
        enabled=True,
        prompt_profile="general",
    )

    out = analyzer.analyze("Is there a person on the bed?")

    assert out["summary"] == "No, there is no person visible on the bed."
    assert out["structured"]["occupied"] is False
    assert out["structured"]["bed_state"] == "partial"


class SpecificQuestionRetryLLM(FakeLLM):
    def generate_raw(self, *, prompt, images_b64=None, temperature=0.2, num_predict=None):
        return {
            "response": (
                '{"occupied": false,'
                '"bed_state": "made",'
                '"desk_state": "active",'
                '"focus_readiness": 0.6,'
                '"sleep_readiness": 0.5,'
                '"issues": ["Laundry on floor"],'
                '"query_answer": "Room appears occupied. The desk looks actively in use.",'
                '"summary": "Room appears occupied. The desk looks actively in use."}'
            ),
            "done_reason": "stop",
        }

    def generate_json(
        self, *, prompt, schema=None, images_b64=None, temperature=0.2, num_predict=None
    ):
        assert "Answer this exact visual question" in prompt
        return {
            "occupied": False,
            "bed_state": "made",
            "desk_state": "active",
            "focus_readiness": 0.6,
            "sleep_readiness": 0.5,
            "issues": ["Laundry on floor"],
            "query_answer": "Yes, a monitor is visible on the desk.",
            "summary": "The room looks occupied and the desk is active.",
        }


def test_room_analyzer_strips_generic_suffix_and_retries_for_specific_question(tmp_path):
    image_path = tmp_path / "bedroom.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    source = BedroomImageSource(
        base_url="http://localhost:8123",
        token="",
        camera_mode="file",
        camera_entity_id="",
        camera_device="/dev/video0",
        camera_width=640,
        camera_height=480,
        camera_skip_frames=30,
        fallback_image_path=str(image_path),
    )
    analyzer = BedroomRoomAnalyzer(
        kv=kv,
        llm=SpecificQuestionRetryLLM(),
        image_source=source,
        enabled=True,
        prompt_profile="general",
    )

    out = analyzer.analyze("Do you see a monitor on the desk? analyze bedroom")

    assert out["summary"] == "Yes, a monitor is visible on the desk."
    assert out["structured"]["query"] == "Do you see a monitor on the desk"
    assert out["structured"]["raw_query"] == "Do you see a monitor on the desk? analyze bedroom"


class BedAdviceFakeLLM(FakeLLM):
    def generate_raw(self, *, prompt, images_b64=None, temperature=0.2, num_predict=None):
        return {
            "response": (
                '{"occupied": false,'
                '"bed_state":"unmade",'
                '"desk_state":"active",'
                '"focus_readiness":0.7,'
                '"sleep_readiness":0.3,'
                '"issues":["Laundry bag on floor","Laundry pile near bed","Laptop and items on desk"],'
                '"query_answer":"Fold and flatten the blanket neatly on the bed.",'
                '"summary":"Room shows active workspace with unmade bed and minor clutter."}'
            ),
            "done_reason": "stop",
        }


def test_room_analyzer_softens_bed_state_for_advice_query(tmp_path):
    image_path = tmp_path / "bedroom.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    source = BedroomImageSource(
        base_url="http://localhost:8123",
        token="",
        camera_mode="file",
        camera_entity_id="",
        camera_device="/dev/video0",
        camera_width=640,
        camera_height=480,
        camera_skip_frames=30,
        fallback_image_path=str(image_path),
    )
    analyzer = BedroomRoomAnalyzer(
        kv=kv,
        llm=BedAdviceFakeLLM(),
        image_source=source,
        enabled=True,
        prompt_profile="general",
    )

    out = analyzer.analyze("What to do to make complete the bed")

    assert out["structured"]["bed_state"] == "made"
    assert out["summary"] == "Fold and flatten the blanket neatly on the bed."


def test_image_source_device_mode_captures_and_persists_snapshot(tmp_path, monkeypatch):
    output_path = tmp_path / "latest.jpg"
    debug_dir = tmp_path / "debug"

    def fake_run(cmd, capture_output, text, timeout, check):
        Path(cmd[-1]).write_bytes(b"captured-image")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    from pathlib import Path

    monkeypatch.setattr("vision.image_source.subprocess.run", fake_run)

    source = BedroomImageSource(
        base_url="http://localhost:8123",
        token="",
        camera_mode="device",
        camera_entity_id="",
        camera_device="/dev/video0",
        camera_width=640,
        camera_height=480,
        camera_skip_frames=30,
        fallback_image_path=str(output_path),
        debug_save_dir=str(debug_dir),
    )

    out = source.get_bedroom_image()

    assert out["ok"] is True
    assert out["source"] == "device"
    assert output_path.read_bytes() == b"captured-image"
    assert out["debug_path"] is not None
    assert out["captured_at_ms"] > 0
    assert out["image_sha256"]
    assert len(list(debug_dir.glob("bedroom-debug-*.jpg"))) == 1


def test_image_source_device_mode_does_not_silently_fallback(tmp_path, monkeypatch):
    output_path = tmp_path / "latest.jpg"
    output_path.write_bytes(b"stale-image")

    def fake_run(cmd, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="camera unavailable")

    monkeypatch.setattr("vision.image_source.subprocess.run", fake_run)

    source = BedroomImageSource(
        base_url="http://localhost:8123",
        token="",
        camera_mode="device",
        camera_entity_id="",
        camera_device="/dev/video0",
        camera_width=640,
        camera_height=480,
        camera_skip_frames=30,
        fallback_image_path=str(output_path),
    )

    out = source.get_bedroom_image()

    assert out["ok"] is False
    assert out["source"] == "device"
    assert "camera unavailable" in out["detail"]
