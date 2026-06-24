"""HTTP server for shadow_root."""

from __future__ import annotations

import errno
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import ShadowConfig
from .session import ShadowSession
from .tunnel import SshReverseTunnel, tunnel_access_url
from .web_ui import INDEX_HTML


class ShadowHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], session: ShadowSession) -> None:
        super().__init__(server_address, _ShadowHandler)
        self.session = session


class _ShadowHandler(BaseHTTPRequestHandler):
    server: ShadowHTTPServer

    def do_GET(self) -> None:
        started = time.monotonic()
        if not self._authorized():
            self._log("GET unauthorized", path=self.path)
            self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        path = urlparse(self.path).path
        self._log("GET start", path=path)
        if path == "/":
            self._send_html(INDEX_HTML)
        elif path == "/stream.mjpg":
            self._send_mjpeg()
            self._log("GET stream end", path=path, ms=_elapsed_ms(started))
        elif path == "/frame.png":
            try:
                frame = self.server.session.frame_png()
            except Exception as exc:
                self._log("GET frame error", path=path, error=str(exc))
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", self.server.session.display_streamer.content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._write_body(frame)
            self._log("GET frame ok", path=path, bytes=len(frame), ms=_elapsed_ms(started))
        elif path == "/status":
            payload = self.server.session.status()
            self._send_json(payload)
            self._log("GET status ok", path=path, recording=self.server.session.is_recording, ms=_elapsed_ms(started))
        else:
            self._log("GET not found", path=path)
            self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        started = time.monotonic()
        if not self._authorized():
            self._log("POST unauthorized", path=self.path)
            self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            self._log("POST start", path=path, summary=_payload_summary(payload))
            if path == "/recording/start":
                result = self.server.session.start_recording()
                self._send_json(result)
                self._log("POST recording start ok", path=path, result=result, ms=_elapsed_ms(started))
            elif path == "/recording/stop":
                bundle = self.server.session.stop_recording()
                self._send_json({"ok": True, "bundle": bundle})
                self._log("POST recording stop ok", path=path, operations=len(bundle.get("operations", [])), ms=_elapsed_ms(started))
            elif path == "/event":
                result = self.server.session.handle_pointer_event(payload)
                self._send_json(result)
                self._log("POST event ok", path=path, result=result, ms=_elapsed_ms(started))
            elif path == "/events":
                result = self.server.session.handle_pointer_batch(payload)
                self._send_json(result)
                self._log("POST events ok", path=path, result=result, ms=_elapsed_ms(started))
            elif path == "/webrtc/offer":
                result = self.server.session.handle_webrtc_offer(payload)
                status = HTTPStatus.OK if "sdp" in result else HTTPStatus.BAD_REQUEST
                self._send_json(result, status)
                self._log("POST webrtc offer ok", path=path, keys=sorted(result.keys()), ms=_elapsed_ms(started))
            elif path == "/wake":
                result = self.server.session.wake_display()
                self._send_json(result)
                self._log("POST wake ok", path=path, result=result, ms=_elapsed_ms(started))
            else:
                self._log("POST not found", path=path)
                self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._log("POST error", path=path, error=repr(exc), ms=_elapsed_ms(started))
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
            self._log("client disconnected", path=getattr(self, "path", ""), bytes=len(body))
            return

    def _send_mjpeg(self) -> None:
        boundary = "nice-auther-frame"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        frames = 0
        try:
            for frame in self.server.session.mjpeg_frames():
                frames += 1
                chunk = (
                    f"--{boundary}\r\n"
                    f"Content-Type: {self.server.session.display_streamer.content_type}\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                ).encode("ascii") + frame + b"\r\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self._log("stream disconnected", path=self.path, frames=frames)
            return

    def _log(self, message: str, **fields: Any) -> None:
        client_address = getattr(self, "client_address", None)
        payload = {
            "component": "shadow_root.server",
            "message": message,
            "client": client_address[0] if client_address else "",
            **fields,
        }
        print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def start_shadow_session(config: ShadowConfig | None = None, *, session: ShadowSession | None = None) -> ShadowSession:
    shadow = session or ShadowSession(config)
    shadow.prepare()
    bind_host = _bind_host_for_config(shadow.config)
    tunnel = SshReverseTunnel(shadow.config)
    try:
        server = ShadowHTTPServer((bind_host, shadow.config.port), shadow)
    except OSError as exc:
        if exc.errno in {errno.EADDRNOTAVAIL, 49}:
            raise RuntimeError(
                "shadow_root cannot bind to the configured address. "
                "Use SHADOW_BIND_HOST/ShadowConfig.bind_host for the local listen address "
                "(usually 0.0.0.0 or 127.0.0.1), and use SHADOW_HOST/ShadowConfig.host "
                "only for the remote/public access host."
            ) from exc
        raise
    print(f"shadow_root listening on http://{_format_url_host(bind_host)}:{shadow.config.port}")
    access_url = _access_url_for_config(shadow.config)
    if access_url != f"http://{_format_url_host(bind_host)}:{shadow.config.port}":
        print(f"shadow_root access URL: {access_url}")
    if tunnel.enabled:
        tunnel.start()
        print(f"shadow_root tunnel URL: {tunnel_access_url(shadow.config)}")
    try:
        server.serve_forever()
    finally:
        tunnel.stop()
        shadow.close()
        server.server_close()
    return shadow


def _bind_host_for_config(config: ShadowConfig) -> str:
    if config.bind_host:
        return config.bind_host
    if _is_local_bind_host(config.host):
        return config.host
    return "0.0.0.0"


def _access_url_for_config(config: ShadowConfig) -> str:
    host = config.host
    if host in {"", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{_format_url_host(host)}:{config.port}"


def _is_local_bind_host(host: str) -> bool:
    return host in {"", "0.0.0.0", "::", "127.0.0.1", "::1", "localhost"}


def _format_url_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    events = payload.get("events")
    summary: dict[str, Any] = {"keys": sorted(payload.keys())}
    if isinstance(events, list):
        summary["events"] = len(events)
        if events:
            first = events[0] if isinstance(events[0], dict) else {}
            last = events[-1] if isinstance(events[-1], dict) else {}
            summary["first"] = {key: first.get(key) for key in ("type", "pointer_id", "x", "y")}
            summary["last"] = {key: last.get(key) for key in ("type", "pointer_id", "x", "y")}
    else:
        summary.update({key: payload.get(key) for key in ("type", "pointer_id", "x", "y") if key in payload})
    return summary
