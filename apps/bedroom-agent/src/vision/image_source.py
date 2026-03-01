from __future__ import annotations

import hashlib
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class BedroomImageSource:
    base_url: str
    token: str
    camera_mode: str
    camera_entity_id: str
    camera_device: str
    camera_width: int
    camera_height: int
    camera_skip_frames: int
    fallback_image_path: str
    debug_save_dir: str = ""

    def get_bedroom_image(self) -> dict[str, Any]:
        mode = self.camera_mode.strip().lower()
        if mode == "ha_snapshot":
            snapshot = self._fetch_ha_snapshot()
            if snapshot["ok"]:
                return snapshot

            fallback = self._read_fallback_file(error_detail=snapshot["detail"])
            if fallback["ok"]:
                return fallback
            return snapshot

        if mode == "file":
            return self._read_fallback_file(error_detail="camera_mode=file")

        if mode == "device":
            capture = self._capture_device_snapshot()
            return capture

        return {
            "ok": False,
            "source": mode or "unknown",
            "detail": f"unsupported_camera_mode:{self.camera_mode}",
        }

    def _capture_device_snapshot(self) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        cmd = [
            "fswebcam",
            "-d",
            self.camera_device,
            "-r",
            f"{int(self.camera_width)}x{int(self.camera_height)}",
            "--no-banner",
            "-S",
            str(int(self.camera_skip_frames)),
            str(tmp_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            tmp_path.unlink(missing_ok=True)
            return {"ok": False, "source": "device", "detail": f"device_capture_failed:{exc}"}

        if result.returncode != 0 or (not tmp_path.exists()):
            stderr = (result.stderr or result.stdout or "").strip()
            tmp_path.unlink(missing_ok=True)
            return {
                "ok": False,
                "source": "device",
                "detail": f"device_capture_failed:{stderr or 'fswebcam_returned_nonzero'}",
            }

        image_bytes = tmp_path.read_bytes()
        tmp_path.unlink(missing_ok=True)
        captured_at_ms = int(time.time() * 1000)
        self._persist_latest_snapshot(image_bytes)
        debug_path = self._persist_debug_snapshot(image_bytes)
        return {
            "ok": True,
            "source": "device",
            "detail": f"captured_from_{self.camera_device}",
            "content_type": "image/jpeg",
            "image_bytes": image_bytes,
            "device": self.camera_device,
            "captured_at_ms": captured_at_ms,
            "debug_path": debug_path,
            "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
        }

    def _fetch_ha_snapshot(self) -> dict[str, Any]:
        if not self.camera_entity_id.strip():
            return {"ok": False, "source": "ha_snapshot", "detail": "camera_entity_id_missing"}
        if not self.token.strip():
            return {"ok": False, "source": "ha_snapshot", "detail": "ha_token_missing"}

        url = self.base_url.rstrip("/") + f"/api/camera_proxy/{self.camera_entity_id}"
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=(3.0, 20.0),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            return {"ok": False, "source": "ha_snapshot", "detail": f"ha_snapshot_failed:{exc}"}

        content_type = response.headers.get("Content-Type", "image/jpeg")
        return {
            "ok": True,
            "source": "ha_snapshot",
            "detail": "fetched_from_home_assistant",
            "content_type": content_type,
            "image_bytes": response.content,
        }

    def _read_fallback_file(self, error_detail: str) -> dict[str, Any]:
        path_text = self.fallback_image_path.strip()
        if not path_text:
            return {
                "ok": False,
                "source": "file",
                "detail": f"fallback_image_missing:{error_detail}",
            }
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            app_root = Path(__file__).resolve().parents[2]
            path = app_root / path
        path = path.resolve(strict=False)
        if not path.exists():
            return {"ok": False, "source": "file", "detail": f"fallback_image_not_found:{path}"}

        return {
            "ok": True,
            "source": "file",
            "detail": f"read_from_file_after:{error_detail}",
            "content_type": self._guess_content_type(path),
            "image_bytes": path.read_bytes(),
            "path": str(path),
        }

    def _persist_latest_snapshot(self, image_bytes: bytes) -> None:
        path_text = self.fallback_image_path.strip()
        if not path_text:
            return

        path = Path(path_text).expanduser()
        if not path.is_absolute():
            app_root = Path(__file__).resolve().parents[2]
            path = app_root / path
        path = path.resolve(strict=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes)

    def _persist_debug_snapshot(self, image_bytes: bytes) -> str | None:
        dir_text = self.debug_save_dir.strip()
        if not dir_text:
            return None

        directory = Path(dir_text).expanduser()
        if not directory.is_absolute():
            app_root = Path(__file__).resolve().parents[2]
            directory = app_root / directory
        directory = directory.resolve(strict=False)
        directory.mkdir(parents=True, exist_ok=True)

        filename = f"bedroom-debug-{int(time.time() * 1000)}.jpg"
        path = directory / filename
        path.write_bytes(image_bytes)
        return str(path)

    def _guess_content_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".png":
            return "image/png"
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        return "application/octet-stream"
