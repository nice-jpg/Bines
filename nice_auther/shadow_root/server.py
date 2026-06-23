"""HTTP server for shadow_root."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ShadowConfig
from .session import ShadowSession
from .web_ui import INDEX_HTML


class ShadowHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], session: ShadowSession) -> None:
        super().__init__(server_address, _ShadowHandler)
        self.session = session


class _ShadowHandler(BaseHTTPRequestHandler):
    server: ShadowHTTPServer

    def do_GET(self) -> None:
        if not self._authorized():
            self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(INDEX_HTML)
        elif path == "/stream.mjpg":
            self._send_mjpeg()
        elif path == "/frame.png":
            try:
                frame = self.server.session.frame_png()
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", self.server.session.display_streamer.content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._write_body(frame)
        elif path == "/status":
            session = self.server.session
            self._send_json(
                {
                    "ok": True,
                    "recording": session.is_recording,
                    "screen": {"width": session.screen_width, "height": session.screen_height},
                    "frame_interval_ms": session.config.frame_interval_ms,
                    "video": {
                        "backend": session.config.video_backend,
                        "format": session.config.video_format,
                        "quality": session.config.video_quality,
                        "scale": session.config.video_scale,
                    },
                }
            )
        else:
            self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._authorized():
            self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/recording/start":
                self._send_json(self.server.session.start_recording())
            elif path == "/recording/stop":
                self._send_json({"ok": True, "bundle": self.server.session.stop_recording()})
            elif path == "/event":
                self._send_json(self.server.session.handle_pointer_event(payload))
            elif path == "/events":
                self._send_json(self.server.session.handle_pointer_batch(payload))
            else:
                self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _authorized(self) -> bool:
        token = self.server.session.config.token
        if not token:
            return True
        parsed = urlparse(self.path)
        query_token = parse_qs(parsed.query).get("token", [""])[0]
        header_token = self.headers.get("X-Shadow-Token", "")
        return token in {query_token, header_token}

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    def _write_body(self, body: bytes) -> None:
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def _send_mjpeg(self) -> None:
        boundary = "nice-auther-frame"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for frame in self.server.session.mjpeg_frames():
                chunk = (
                    f"--{boundary}\r\n"
                    f"Content-Type: {self.server.session.display_streamer.content_type}\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                ).encode("ascii") + frame + b"\r\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def start_shadow_session(config: ShadowConfig | None = None, *, session: ShadowSession | None = None) -> ShadowSession:
    shadow = session or ShadowSession(config)
    shadow.prepare()
    server = ShadowHTTPServer((shadow.config.host, shadow.config.port), shadow)
    print(f"shadow_root listening on http://{shadow.config.host}:{shadow.config.port}")
    try:
        server.serve_forever()
    finally:
        shadow.close()
        server.server_close()
    return shadow
