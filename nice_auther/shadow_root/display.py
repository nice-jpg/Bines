"""Display streaming backends for shadow_root."""

from __future__ import annotations

from io import BytesIO
import threading
import time
from typing import Iterator

from .adb import AdbClient
from .config import ShadowConfig


class DisplayStreamer:
    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def latest_frame(self, timeout: float | None = None) -> bytes:
        raise NotImplementedError

    def mjpeg_frames(self) -> Iterator[bytes]:
        raise NotImplementedError

    @property
    def content_type(self) -> str:
        return "image/png"


class MjpegScreencapStreamer(DisplayStreamer):
    def __init__(self, adb: AdbClient, config: ShadowConfig) -> None:
        self.adb = adb
        self.config = config
        self._condition = threading.Condition()
        self._latest_frame = b""
        self._latest_version = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.capture_count = 0
        self.max_active_captures = 0
        self._active_captures = 0
        self._content_type = _content_type_for_config(config)

    @property
    def content_type(self) -> str:
        return self._content_type

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=2)

    def latest_frame(self, timeout: float | None = None) -> bytes:
        self.start()
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._latest_frame:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if remaining == 0:
                    break
                self._condition.wait(timeout=remaining)
            return self._latest_frame

    def mjpeg_frames(self) -> Iterator[bytes]:
        self.start()
        seen_version = 0
        while not self._stop.is_set():
            with self._condition:
                self._condition.wait_for(
                    lambda: self._latest_version != seen_version or self._stop.is_set(),
                    timeout=2,
                )
                if self._stop.is_set():
                    return
                if not self._latest_frame:
                    continue
                frame = self._latest_frame
                seen_version = self._latest_version
            yield frame

    def _capture_loop(self) -> None:
        fps = max(1, int(self.config.video_fps))
        delay = 1.0 / fps
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                with self._condition:
                    self._active_captures += 1
                    self.max_active_captures = max(self.max_active_captures, self._active_captures)
                frame = _transform_frame(self.adb.screencap_png(), self.config)
                with self._condition:
                    self.capture_count += 1
                    self._latest_frame = frame
                    self._latest_version += 1
                    self._condition.notify_all()
            except Exception:
                time.sleep(min(1.0, delay))
            finally:
                with self._condition:
                    self._active_captures = max(0, self._active_captures - 1)
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.0, delay - elapsed))


class WebRtcH264Streamer(DisplayStreamer):
    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def latest_frame(self, timeout: float | None = None) -> bytes:
        raise RuntimeError("webrtc_h264 backend does not expose PNG frames")

    def mjpeg_frames(self) -> Iterator[bytes]:
        raise RuntimeError("webrtc_h264 backend does not expose MJPEG frames")


def create_display_streamer(adb: AdbClient, config: ShadowConfig) -> DisplayStreamer:
    backend = config.video_backend.strip().lower()
    if backend == "mjpeg_screencap":
        return MjpegScreencapStreamer(adb, config)
    if backend in {"webrtc_h264", "scrcpy_h264"}:
        return WebRtcH264Streamer()
    raise ValueError(f"unsupported video backend: {config.video_backend}")


def _content_type_for_config(config: ShadowConfig) -> str:
    if config.video_format.strip().lower() in {"jpg", "jpeg"}:
        return "image/jpeg"
    return "image/png"


def _transform_frame(frame: bytes, config: ShadowConfig) -> bytes:
    video_format = config.video_format.strip().lower()
    scale = max(0.05, min(1.0, float(config.video_scale)))
    quality = max(1, min(95, int(config.video_quality)))
    if video_format not in {"jpg", "jpeg", "png"}:
        video_format = "jpeg"
    if scale >= 0.999 and video_format == "png":
        return frame

    try:
        from PIL import Image
    except Exception:
        return frame

    try:
        with Image.open(BytesIO(frame)) as image:
            if scale < 0.999:
                width = max(1, int(image.width * scale))
                height = max(1, int(image.height * scale))
                image = image.resize((width, height), Image.Resampling.BILINEAR)
            output = BytesIO()
            if video_format in {"jpg", "jpeg"}:
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(output, format="JPEG", quality=quality, optimize=True)
            else:
                image.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except Exception:
        return frame
