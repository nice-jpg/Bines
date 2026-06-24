from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch

from nice_auther.shadow_root import AdbClient, ShadowConfig, ShadowSession
from nice_auther.shadow_root import config as shadow_config
from nice_auther.shadow_root.android_agent import AndroidShadowAgent
from nice_auther.shadow_root.display import MjpegScreencapStreamer, WebRtcH264Streamer, create_display_streamer
from nice_auther.shadow_root.input_stream import ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_PRESSURE, PIAR_MAGIC, TouchEventEncoder, parse_input_capabilities
from nice_auther.shadow_root.reachability import ReachabilityResult, log_reachability_result
from nice_auther.shadow_root.run import _config_from_args
from nice_auther.shadow_root.server import _ShadowHandler, _access_url_for_config, _bind_host_for_config, _payload_summary, start_shadow_session
from nice_auther.shadow_root.tunnel import SshReverseTunnel, tunnel_access_url
from nice_auther.shadow_root.web_ui import INDEX_HTML
from nice_auther.shadow_root.webrtc import DEFAULT_WEBRTC_GATEWAY_PATH, WebRtcGateway


class FakeStdin(BytesIO):
    def flush(self) -> None:
        return


class FakeStdout:
    def __iter__(self):
        return iter(
            [
                "[ 1.000000] /dev/input/event3: EV_KEY BTN_TOUCH DOWN\n",
                "[ 1.000000] /dev/input/event3: EV_SYN SYN_REPORT 00000000\n",
            ]
        )


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()
        self.stderr = BytesIO()
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True

    def poll(self) -> None:
        return None


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if args[-3:] == ["exec-out", "screencap", "-p"]:
            return subprocess.CompletedProcess(args, 0, "PNGDATA", "")
        if args[-2:] == ["wm", "size"]:
            return subprocess.CompletedProcess(args, 0, "Physical size: 1080x2400\n", "")
        if args[-2:] == ["getprop", "ro.product.model"]:
            return subprocess.CompletedProcess(args, 0, "Pixel Test\n", "")
        if args[-2:] == ["getprop", "ro.product.brand"]:
            return subprocess.CompletedProcess(args, 0, "Google\n", "")
        if args[-2:] == ["getprop", "ro.build.version.sdk"]:
            return subprocess.CompletedProcess(args, 0, "35\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")


class RecordingBinaryRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"\x89PNGDATA", b"")


def sample_png() -> bytes:
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (20, 20), color=(255, 0, 0)).save(output, format="PNG")
    return output.getvalue()


class NiceAutherShadowRootTests(unittest.TestCase):
    def test_importing_server_does_not_start_shadow_session(self) -> None:
        sys.modules.pop("nice_auther.shadow_root.server", None)
        with patch("nice_auther.shadow_root.session.ShadowSession.prepare") as prepare:
            __import__("nice_auther.shadow_root.server")

        prepare.assert_not_called()

    def test_config_reads_environment(self) -> None:
        config = ShadowConfig.from_env(
            {
                "SHADOW_HOST": "0.0.0.0",
                "SHADOW_BIND_HOST": "127.0.0.1",
                "SHADOW_PORT": "9000",
                "SHADOW_ADB_PATH": "/opt/adb",
                "SHADOW_ADB_SERIAL": "device-1",
                "SHADOW_INPUT_DEVICE": "/dev/input/event9",
                "SHADOW_FRAME_INTERVAL_MS": "250",
                "SHADOW_TOKEN": "secret",
                "SHADOW_VIDEO_BACKEND": "mjpeg_screencap",
            }
        )

        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.bind_host, "127.0.0.1")
        self.assertEqual(config.port, 9000)
        self.assertEqual(config.adb_path, "/opt/adb")
        self.assertEqual(config.adb_serial, "device-1")
        self.assertEqual(config.input_device, "/dev/input/event9")
        self.assertEqual(config.frame_interval_ms, 250)
        self.assertEqual(config.token, "secret")
        self.assertEqual(ShadowConfig.from_env({}).video_backend, "webrtc_h264")
        self.assertEqual(ShadowConfig.from_env({"SHADOW_VIDEO_FPS": "12"}).video_fps, 12)
        quality_config = ShadowConfig.from_env(
            {
                "SHADOW_VIDEO_FORMAT": "png",
                "SHADOW_VIDEO_QUALITY": "70",
                "SHADOW_VIDEO_SCALE": "0.4",
                "SHADOW_VIDEO_MAX_SIZE": "540",
                "SHADOW_VIDEO_BITRATE": "900k",
                "SHADOW_VIDEO_IFRAME_INTERVAL_MS": "750",
                "SHADOW_WEBRTC_GATEWAY_PATH": "/tmp/gateway",
                "SHADOW_WEBRTC_GATEWAY_MANAGED": "false",
                "SHADOW_WEBRTC_ICE_PUBLIC_IP": "192.168.1.20",
                "SHADOW_WEBRTC_ICE_UDP_PORT_MIN": "30000",
                "SHADOW_WEBRTC_ICE_UDP_PORT_MAX": "30010",
                "SHADOW_ANDROID_AGENT_JAR": "/tmp/agent.jar",
                "SHADOW_ANDROID_AGENT_MAIN_CLASS": "example.Agent",
                "SHADOW_WEBRTC_RTP_MTU": "1000",
                "SHADOW_WEBRTC_TRANSPORT": "udp_rtp",
                "SHADOW_WEBRTC_RTP_HOST": "192.168.1.10",
                "SHADOW_WEBRTC_RTP_LISTEN_HOST": "0.0.0.0",
                "SHADOW_ANDROID_AGENT_SELF_TEST_RTP": "true",
            }
        )
        self.assertEqual(quality_config.video_format, "png")
        self.assertEqual(quality_config.video_quality, 70)
        self.assertEqual(quality_config.video_scale, 0.4)
        self.assertEqual(quality_config.video_max_size, 540)
        self.assertEqual(quality_config.video_bitrate, "900k")
        self.assertEqual(quality_config.video_iframe_interval_ms, 750)
        self.assertEqual(quality_config.webrtc_gateway_path, "/tmp/gateway")
        self.assertFalse(quality_config.webrtc_gateway_managed)
        self.assertEqual(quality_config.webrtc_ice_public_ip, "192.168.1.20")
        self.assertEqual(quality_config.webrtc_ice_udp_port_min, 30000)
        self.assertEqual(quality_config.webrtc_ice_udp_port_max, 30010)
        self.assertEqual(quality_config.android_agent_jar, "/tmp/agent.jar")
        self.assertEqual(quality_config.android_agent_main_class, "example.Agent")
        self.assertEqual(quality_config.webrtc_rtp_mtu, 1000)
        self.assertEqual(quality_config.webrtc_transport, "udp_rtp")
        self.assertEqual(quality_config.webrtc_rtp_host, "192.168.1.10")
        self.assertEqual(quality_config.webrtc_rtp_listen_host, "0.0.0.0")
        self.assertTrue(quality_config.android_agent_self_test_rtp)
        tunnel_config = ShadowConfig.from_env(
            {
                "SHADOW_TUNNEL_ENABLED": "true",
                "SHADOW_TUNNEL_SSH_HOST": "user@remote.example.com",
                "SHADOW_TUNNEL_SSH_PORT": "2222",
                "SHADOW_TUNNEL_SSH_KEY": "/tmp/key",
                "SHADOW_TUNNEL_REMOTE_BIND_HOST": "0.0.0.0",
                "SHADOW_TUNNEL_REMOTE_PORT": "19000",
                "SHADOW_TUNNEL_LOCAL_HOST": "127.0.0.1",
                "SHADOW_TUNNEL_EXTRA_ARGS": "-o StrictHostKeyChecking=no",
            }
        )
        self.assertTrue(tunnel_config.tunnel_enabled)
        self.assertEqual(tunnel_config.tunnel_ssh_host, "user@remote.example.com")
        self.assertEqual(tunnel_config.tunnel_ssh_port, 2222)
        self.assertEqual(tunnel_config.tunnel_ssh_key, "/tmp/key")
        self.assertEqual(tunnel_config.tunnel_remote_port, 19000)
        self.assertEqual(tunnel_config.tunnel_extra_args, "-o StrictHostKeyChecking=no")

    def test_config_reads_shadow_root_env_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_dir = Path(tmp)
            (env_dir / "10-default.env").write_text(
                "\n".join(
                    [
                        "# comment",
                        "SHADOW_PORT=9001",
                        "export SHADOW_WEBRTC_TRANSPORT=tcp_direct",
                        "SHADOW_WEBRTC_GATEWAY_MANAGED=false",
                        "SHADOW_WEBRTC_RTP_HOST='139.224.44.6'",
                    ]
                ),
                encoding="utf-8",
            )
            (env_dir / "20-local.env").write_text("SHADOW_PORT=9002\n", encoding="utf-8")

            with patch.object(shadow_config, "ENV_DIR", env_dir), patch.dict(os.environ, {}, clear=True):
                config = ShadowConfig.from_env()

            self.assertEqual(config.port, 9002)
            self.assertEqual(config.webrtc_transport, "tcp_direct")
            self.assertFalse(config.webrtc_gateway_managed)
            self.assertEqual(config.webrtc_rtp_host, "139.224.44.6")

    def test_os_environment_overrides_shadow_root_env_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_dir = Path(tmp)
            (env_dir / "shadow_root.env").write_text("SHADOW_PORT=9001\n", encoding="utf-8")

            with patch.object(shadow_config, "ENV_DIR", env_dir), patch.dict(os.environ, {"SHADOW_PORT": "9999"}, clear=True):
                config = ShadowConfig.from_env()

            self.assertEqual(config.port, 9999)

    def test_checked_in_shadow_root_env_only_exposes_active_direct_webrtc_variables(self) -> None:
        env_text = (Path(__file__).resolve().parents[1] / "nice_auther/shadow_root/env/shadow_root.env").read_text(encoding="utf-8")

        self.assertIn("SHADOW_WEBRTC_TRANSPORT=tcp_direct", env_text)
        self.assertIn("SHADOW_WEBRTC_GATEWAY_HOST=139.224.44.6", env_text)
        self.assertIn("SHADOW_ANDROID_AGENT_JAR=", env_text)
        self.assertNotIn("SHADOW_TUNNEL_", env_text)
        self.assertNotIn("SHADOW_FRAME_INTERVAL_MS", env_text)
        self.assertNotIn("SHADOW_VIDEO_FORMAT", env_text)
        self.assertNotIn("SHADOW_WEBRTC_RTP_LISTEN_HOST", env_text)
        self.assertNotIn("SHADOW_WEBRTC_GATEWAY_PATH", env_text)

    def test_remote_public_host_binds_to_wildcard_by_default(self) -> None:
        config = ShadowConfig(host="remote.example.com", port=9000)

        self.assertEqual(_bind_host_for_config(config), "0.0.0.0")
        self.assertEqual(_access_url_for_config(config), "http://remote.example.com:9000")

    def test_explicit_bind_host_wins_over_public_host(self) -> None:
        config = ShadowConfig(host="remote.example.com", bind_host="127.0.0.1", port=9000)

        self.assertEqual(_bind_host_for_config(config), "127.0.0.1")

    def test_reverse_tunnel_builds_ssh_command(self) -> None:
        config = ShadowConfig(
            host="remote.example.com",
            port=8765,
            tunnel_enabled=True,
            tunnel_ssh_host="user@remote.example.com",
            tunnel_ssh_port=2222,
            tunnel_ssh_key="/tmp/key",
            tunnel_remote_bind_host="0.0.0.0",
            tunnel_remote_port=19000,
            tunnel_local_host="127.0.0.1",
            tunnel_extra_args="-o StrictHostKeyChecking=no",
        )

        command = SshReverseTunnel(config).command()

        self.assertEqual(command[:3], ["ssh", "-N", "-T"])
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertIn("-i", command)
        self.assertIn("/tmp/key", command)
        self.assertIn("StrictHostKeyChecking=no", command)
        self.assertIn("-R", command)
        self.assertIn("0.0.0.0:19000:127.0.0.1:8765", command)
        self.assertEqual(command[-1], "user@remote.example.com")
        self.assertEqual(tunnel_access_url(config), "http://remote.example.com:19000")

    def test_reverse_tunnel_start_stop_lifecycle(self) -> None:
        started: list[list[str]] = []
        process = FakeProcess()
        config = ShadowConfig(tunnel_enabled=True, tunnel_ssh_host="remote.example.com")

        tunnel = SshReverseTunnel(
            config,
            popen_factory=lambda args, **kwargs: started.append(args) or process,
        )

        tunnel.start()
        tunnel.stop()

        self.assertEqual(started[0][-1], "remote.example.com")
        self.assertTrue(process.terminated)

    def test_run_script_args_override_environment_config(self) -> None:
        config, agent_only = _config_from_args(
            [
                "--host",
                "remote.example.com",
                "--bind-host",
                "127.0.0.1",
                "--port",
                "19000",
                "--tunnel",
                "--tunnel-ssh-host",
                "user@remote.example.com",
                "--tunnel-remote-port",
                "29000",
            ]
        )

        self.assertEqual(config.host, "remote.example.com")
        self.assertEqual(config.bind_host, "127.0.0.1")
        self.assertEqual(config.port, 19000)
        self.assertTrue(config.tunnel_enabled)
        self.assertEqual(config.tunnel_ssh_host, "user@remote.example.com")
        self.assertEqual(config.tunnel_remote_port, 29000)
        self.assertFalse(agent_only)

    def test_run_script_agent_only_flag(self) -> None:
        config, agent_only = _config_from_args(["--agent-only", "--webrtc-rtp-host", "139.224.44.6"])

        self.assertTrue(agent_only)
        self.assertEqual(config.webrtc_rtp_host, "139.224.44.6")

    def test_start_shadow_session_starts_and_stops_tunnel(self) -> None:
        events: list[str] = []

        class FakeTunnel:
            enabled = True

            def __init__(self, config: ShadowConfig) -> None:
                events.append(f"tunnel:init:{config.tunnel_ssh_host}")

            def start(self) -> None:
                events.append("tunnel:start")

            def stop(self) -> None:
                events.append("tunnel:stop")

        class FakeServer:
            def __init__(self, address: tuple[str, int], session: object) -> None:
                events.append(f"server:init:{address[0]}:{address[1]}")

            def serve_forever(self) -> None:
                events.append("server:serve")

            def server_close(self) -> None:
                events.append("server:close")

        session = SimpleNamespace(
            config=ShadowConfig(port=8765, tunnel_enabled=True, tunnel_ssh_host="remote.example.com"),
            prepare=lambda: events.append("session:prepare"),
            close=lambda: events.append("session:close"),
        )

        with (
            patch.dict(
                start_shadow_session.__globals__,
                {"SshReverseTunnel": FakeTunnel, "ShadowHTTPServer": FakeServer},
            ),
            patch("sys.stdout", new_callable=StringIO),
        ):
            start_shadow_session(session=session)

        self.assertEqual(
            events,
            [
                "session:prepare",
                "tunnel:init:remote.example.com",
                "server:init:127.0.0.1:8765",
                "tunnel:start",
                "server:serve",
                "tunnel:stop",
                "session:close",
                "server:close",
            ],
        )

    def test_session_maps_client_coordinates_to_device_coordinates(self) -> None:
        session = ShadowSession(ShadowConfig())
        session.screen_width = 1080
        session.screen_height = 2400

        self.assertEqual(session.map_client_point({"x": 50, "y": 25, "width": 100, "height": 50}), (540, 1200))
        self.assertEqual(session.map_client_point({"x": -1, "y": 9999, "width": 100, "height": 50}), (0, 2399))

    def test_recording_start_stop_builds_bundle_with_getevent_log(self) -> None:
        runner = RecordingRunner()
        process = FakeProcess()
        config = ShadowConfig(input_device="/dev/input/event3", video_backend="mjpeg_screencap")
        adb = AdbClient(config, runner=runner, popen_factory=lambda *args, **kwargs: process)
        session = ShadowSession(config, adb=adb)

        session.prepare()
        started = session.start_recording()
        bundle = session.stop_recording()

        self.assertTrue(started["ok"])
        self.assertTrue(process.terminated)
        self.assertEqual(bundle["schema_version"], 1)
        self.assertEqual(bundle["screen"], {"width": 1080, "height": 2400})
        self.assertEqual(bundle["device_info"]["model"], "Pixel Test")
        self.assertIn("BTN_TOUCH DOWN", bundle["raw_getevent_log"])
        self.assertTrue(any(call[:2] == ["adb", "push"] for call in runner.calls))

    def test_pointer_events_stream_piar_frames_and_record_audit(self) -> None:
        runner = RecordingRunner()
        process = FakeProcess()
        config = ShadowConfig()
        adb = AdbClient(config, runner=runner, popen_factory=lambda *args, **kwargs: process)
        session = ShadowSession(config, adb=adb)
        session.screen_width = 1080
        session.screen_height = 2400
        session.input_capabilities = parse_input_capabilities("", (1080, 2400))
        session.touch_encoder = TouchEventEncoder(session.input_capabilities, (1080, 2400))
        session.start_recording()

        result = session.handle_pointer_batch(
            {
                "events": [
                    {"type": "pointerdown", "pointer_id": 1, "x": 10, "y": 10, "width": 100, "height": 100, "client_time_ms": 1},
                    {"type": "pointermove", "pointer_id": 1, "x": 20, "y": 25, "width": 100, "height": 100, "client_time_ms": 5},
                    {"type": "pointerup", "pointer_id": 1, "x": 30, "y": 40, "width": 100, "height": 100, "client_time_ms": 9},
                ]
            }
        )

        self.assertEqual(result["accepted"], 3)
        self.assertEqual(result["injector"]["written_bytes"], result["frames_bytes"])
        self.assertTrue(process.stdin.getvalue().startswith(PIAR_MAGIC))
        self.assertNotIn(["adb", "shell", "input", "tap", "108", "240"], runner.calls)
        self.assertEqual(session.operations[-1]["type"], "pointerup")
        self.assertTrue(session.build_bundle()["piar_base64"])
        session.stop_recording()

    def test_frame_png_uses_adb_exec_out(self) -> None:
        runner = RecordingBinaryRunner()
        config = ShadowConfig(video_backend="mjpeg_screencap")
        adb = AdbClient(config, binary_runner=runner)
        session = ShadowSession(config, adb=adb)

        self.assertEqual(session.frame_png(), b"\x89PNGDATA")
        self.assertIn(["adb", "exec-out", "screencap", "-p"], runner.calls)

    def test_response_write_ignores_client_disconnect(self) -> None:
        class ResettingWriter(BytesIO):
            def write(self, value: bytes) -> int:
                raise ConnectionResetError("client closed")

        handler = object.__new__(_ShadowHandler)
        handler.wfile = ResettingWriter()

        with patch("sys.stdout", new_callable=StringIO):
            self.assertIsNone(handler._write_body(b"frame"))

    def test_web_ui_supports_real_mobile_touch_events(self) -> None:
        self.assertIn('window.PointerEvent', INDEX_HTML)
        self.assertIn('touchstart', INDEX_HTML)
        self.assertIn('touchmove', INDEX_HTML)
        self.assertIn('touchend', INDEX_HTML)
        self.assertIn('passive: false', INDEX_HTML)
        self.assertIn('ev.preventDefault()', INDEX_HTML)
        self.assertIn('-webkit-touch-callout: none', INDEX_HTML)

    def test_web_ui_batches_pointer_events_until_gesture_end(self) -> None:
        self.assertIn('const activePointers = new Set();', INDEX_HTML)
        self.assertIn('const gestureFlushTimeoutMs = 2500;', INDEX_HTML)
        self.assertIn('function scheduleGestureFlush(gestureEnded = false)', INDEX_HTML)
        self.assertIn('scheduleGestureFlush(type === "pointerup" || type === "pointercancel")', INDEX_HTML)
        self.assertIn('scheduleFlushTimeout()', INDEX_HTML)
        self.assertIn('flush scheduled timeout', INDEX_HTML)
        self.assertIn('if (pendingEvents.length) scheduleGestureFlush(activePointers.size === 0);', INDEX_HTML)

    def test_web_ui_has_visible_debug_log(self) -> None:
        self.assertIn('id="debugLog"', INDEX_HTML)
        self.assertIn('display: none', INDEX_HTML)
        self.assertIn('body.debug-on #debugLog', INDEX_HTML)
        self.assertIn('body.debug-on #screenVideo', INDEX_HTML)
        self.assertIn('function log(', INDEX_HTML)
        self.assertIn('if (!debugEnabled) return;', INDEX_HTML)
        self.assertIn('flush start', INDEX_HTML)
        self.assertIn('POST ${path}', INDEX_HTML)
        self.assertIn('page loaded', INDEX_HTML)
        self.assertIn('id="debugToggle"', INDEX_HTML)
        self.assertIn('params.get("debug") === "1"', INDEX_HTML)
        self.assertIn('params.get("debug") === "true"', INDEX_HTML)
        self.assertIn('localStorage.getItem(debugStorageKey)', INDEX_HTML)
        self.assertIn('function setDebugEnabled(enabled)', INDEX_HTML)
        self.assertIn('document.body.classList.toggle("debug-on", debugEnabled)', INDEX_HTML)
        self.assertIn('debugToggle.onclick', INDEX_HTML)

    def test_web_ui_uses_webrtc_video_and_datachannel(self) -> None:
        self.assertIn('id="screenVideo"', INDEX_HTML)
        self.assertIn('autoplay playsinline muted', INDEX_HTML)
        self.assertIn('object-fit: contain', INDEX_HTML)
        self.assertIn('new RTCPeerConnection()', INDEX_HTML)
        self.assertIn('createDataChannel("control"', INDEX_HTML)
        self.assertIn('waitForIceGatheringComplete(peerConnection)', INDEX_HTML)
        self.assertIn('POST ${path}', INDEX_HTML)
        self.assertIn('"/webrtc/offer"', INDEX_HTML)
        self.assertIn('controlChannel.readyState === "open"', INDEX_HTML)
        self.assertIn('return await post("/events", {events});', INDEX_HTML)
        self.assertIn('startMjpeg()', INDEX_HTML)
        self.assertIn('id="wake"', INDEX_HTML)
        self.assertIn('function wakeDisplay(reason = "manual")', INDEX_HTML)
        self.assertIn('post("/wake", {reason})', INDEX_HTML)
        self.assertIn('wakeDisplay("webrtc-connected")', INDEX_HTML)
        self.assertIn('wakeDisplay("button")', INDEX_HTML)
        self.assertIn('screenVideo.onloadedmetadata', INDEX_HTML)
        self.assertIn('screenVideo.onerror', INDEX_HTML)
        self.assertIn('function sdpCandidates(sdp)', INDEX_HTML)
        self.assertIn('remote ICE candidates', INDEX_HTML)
        self.assertNotIn('id="start"', INDEX_HTML)
        self.assertNotIn('id="stop"', INDEX_HTML)

    def test_server_payload_summary_keeps_event_logs_compact(self) -> None:
        summary = _payload_summary(
            {
                "events": [
                    {"type": "pointerdown", "pointer_id": 1, "x": 10, "y": 20, "width": 100},
                    {"type": "pointerup", "pointer_id": 1, "x": 30, "y": 40, "width": 100},
                ],
            }
        )

        self.assertEqual(summary["events"], 2)
        self.assertEqual(summary["first"], {"type": "pointerdown", "pointer_id": 1, "x": 10, "y": 20})
        self.assertEqual(summary["last"], {"type": "pointerup", "pointer_id": 1, "x": 30, "y": 40})

    def test_touch_event_encoder_scales_browser_pressure_and_normalizes_pointer_id(self) -> None:
        encoder = TouchEventEncoder(parse_input_capabilities("", (100, 200)), (100, 200))

        packet, audits = encoder.encode_batch(
            [
                {"type": "pointerdown", "pointer_id": -1171356434, "x": 10, "y": 20, "width": 100, "height": 200, "pressure": 0.5},
                {"type": "pointerup", "pointer_id": -1171356434, "x": 10, "y": 20, "width": 100, "height": 200, "pressure": 0},
            ]
        )

        self.assertEqual(audits[0]["pointer_id"], 1171356434)
        self.assertEqual(audits[1]["pointer_id"], 1171356434)
        self.assertIn(ABS_MT_PRESSURE.to_bytes(2, "little") + (50).to_bytes(4, "little", signed=True), packet)

    def test_touch_event_encoder_preserves_move_frames(self) -> None:
        capabilities = parse_input_capabilities(
            """
add 1: /dev/input/event3
  ABS_MT_POSITION_X    : value 0, min 0, max 999
  ABS_MT_POSITION_Y    : value 0, min 0, max 1999
  ABS_MT_SLOT          : value 0, min 0, max 9
""",
            (100, 200),
        )
        encoder = TouchEventEncoder(capabilities, (100, 200))
        packet, audits = encoder.encode_batch(
            [
                {"type": "pointerdown", "pointer_id": 1, "x": 0, "y": 0, "width": 100, "height": 200, "client_time_ms": 1},
                {"type": "pointermove", "pointer_id": 1, "x": 50, "y": 100, "width": 100, "height": 200, "client_time_ms": 6},
                {"type": "pointermove", "pointer_id": 1, "x": 80, "y": 120, "width": 100, "height": 200, "client_time_ms": 10},
                {"type": "pointerup", "pointer_id": 1, "x": 100, "y": 200, "width": 100, "height": 200, "client_time_ms": 12},
            ]
        )

        self.assertEqual(len(audits), 4)
        self.assertEqual(audits[1]["device"], {"x": 500, "y": 1000})
        self.assertIn(ABS_MT_POSITION_X.to_bytes(2, "little"), packet)
        self.assertIn(ABS_MT_POSITION_Y.to_bytes(2, "little"), packet)

    def test_display_streamer_reuses_single_capture_worker(self) -> None:
        runner = RecordingBinaryRunner()
        config = ShadowConfig(video_backend="mjpeg_screencap", video_fps=30)
        adb = AdbClient(config, binary_runner=runner)
        streamer = MjpegScreencapStreamer(adb, config)

        streamer.start()
        frame = streamer.latest_frame(timeout=1)
        streamer.stop()

        self.assertEqual(frame, b"\x89PNGDATA")
        self.assertLessEqual(streamer.max_active_captures, 1)

    def test_display_streamer_can_downscale_and_encode_jpeg(self) -> None:
        class PngRunner:
            def __init__(self) -> None:
                self.calls: list[list[str]] = []

            def __call__(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
                self.calls.append(args)
                return subprocess.CompletedProcess(args, 0, sample_png(), b"")

        config = ShadowConfig(video_backend="mjpeg_screencap", video_format="jpeg", video_quality=30, video_scale=0.5, video_fps=30)
        adb = AdbClient(config, binary_runner=PngRunner())
        streamer = MjpegScreencapStreamer(adb, config)

        streamer.start()
        frame = streamer.latest_frame(timeout=1)
        streamer.stop()

        self.assertEqual(streamer.content_type, "image/jpeg")
        self.assertTrue(frame.startswith(b"\xff\xd8"))
        from PIL import Image

        with Image.open(BytesIO(frame)) as image:
            self.assertEqual(image.size, (10, 10))

    def test_webrtc_backend_uses_webrtc_streamer(self) -> None:
        config = ShadowConfig(video_backend="webrtc_h264")
        adb = AdbClient(config, binary_runner=RecordingBinaryRunner())

        self.assertIsInstance(create_display_streamer(adb, config), WebRtcH264Streamer)

    def test_android_agent_pushes_and_starts_root_app_process(self) -> None:
        runner = RecordingRunner()
        started: list[list[str]] = []
        process = FakeProcess()
        with tempfile.TemporaryDirectory() as tmp:
            agent_jar = Path(tmp) / "agent.jar"
            agent_jar.write_bytes(b"jar")
            config = ShadowConfig(
                android_agent_jar=str(agent_jar),
                video_max_size=540,
                video_fps=20,
                video_bitrate="900k",
                video_iframe_interval_ms=750,
                webrtc_rtp_port=19001,
                webrtc_control_port=19002,
                webrtc_rtp_mtu=900,
                android_agent_self_test_rtp=True,
            )
            adb = AdbClient(config, runner=runner, popen_factory=lambda args, **kwargs: started.append(args) or process)

            agent = AndroidShadowAgent(adb, config)
            agent.start()

        self.assertTrue(any(call[:2] == ["adb", "push"] and call[-1] == "/data/local/tmp/nice_shadow_agent.jar" for call in runner.calls))
        command = " ".join(started[0])
        self.assertIn("app_process", command)
        self.assertIn("--transport adb_reverse_tcp", command)
        self.assertIn("--max-size 540", command)
        self.assertIn("--fps 20", command)
        self.assertIn("--bitrate 900k", command)
        self.assertIn("--rtp-port 19001", command)
        self.assertIn("--control-port 19002", command)
        self.assertIn("--mtu 900", command)
        self.assertIn("--self-test-rtp", command)
        agent.stop()
        self.assertTrue(process.terminated)

    def test_android_agent_tcp_direct_uses_configured_remote_host(self) -> None:
        config = ShadowConfig(webrtc_transport="tcp_direct", webrtc_rtp_host="139.224.44.6", webrtc_rtp_port=9081)
        agent = AndroidShadowAgent(AdbClient(config), config)

        args = agent.agent_args()

        self.assertIn("--transport", args)
        self.assertIn("tcp_direct", args)
        self.assertIn("--rtp-host", args)
        self.assertIn("139.224.44.6", args)
        self.assertIn("9081", args)

    def test_webrtc_gateway_builds_external_process_command(self) -> None:
        started: list[list[str]] = []
        process = FakeProcess()
        with tempfile.TemporaryDirectory() as tmp:
            gateway_bin = Path(tmp) / "gateway"
            gateway_bin.write_text("#!/bin/sh\n")
            config = ShadowConfig(webrtc_gateway_path=str(gateway_bin), port=8765)
            gateway = WebRtcGateway(config, popen_factory=lambda args, **kwargs: started.append(args) or process)

            gateway.start()

        self.assertEqual(started[0][0], str(gateway_bin))
        self.assertIn("--transport", started[0])
        self.assertIn("adb_reverse_tcp", started[0])
        self.assertIn("--ice-public-ip", started[0])
        self.assertIn("--ice-udp-port-min", started[0])
        self.assertIn("--rtp-port", started[0])
        self.assertIn("--rtp-listen-host", started[0])
        self.assertIn("0.0.0.0", started[0])
        self.assertIn("9766", started[0])
        self.assertNotIn("--events-url", started[0])
        self.assertNotIn("http://127.0.0.1:8765/events", started[0])
        gateway.stop()
        self.assertTrue(process.terminated)

    def test_webrtc_gateway_uses_bundled_build_path_by_default(self) -> None:
        started: list[list[str]] = []
        process = FakeProcess()
        gateway = WebRtcGateway(ShadowConfig(port=8765), popen_factory=lambda args, **kwargs: started.append(args) or process)

        with patch("nice_auther.shadow_root.webrtc.DEFAULT_WEBRTC_GATEWAY_PATH", Path("/tmp/nice-webrtc-gateway")):
            with patch.object(Path, "exists", return_value=True):
                gateway.start()

        self.assertEqual(started[0][0], "/tmp/nice-webrtc-gateway")
        gateway.stop()

    def test_webrtc_gateway_default_binary_has_been_built(self) -> None:
        self.assertTrue(DEFAULT_WEBRTC_GATEWAY_PATH.exists())

    def test_session_webrtc_offer_is_forwarded_to_gateway(self) -> None:
        class FakeGateway:
            def __init__(self) -> None:
                self.payloads: list[dict[str, str]] = []

            def offer(self, payload: dict[str, str]) -> dict[str, str]:
                self.payloads.append(payload)
                return {"type": "answer", "sdp": "answer-sdp"}

            def status(self) -> dict[str, object]:
                return {"running": True}

            def stop(self) -> None:
                return

        gateway = FakeGateway()
        session = ShadowSession(ShadowConfig(), adb=AdbClient(ShadowConfig()), webrtc_gateway=gateway)

        result = session.handle_webrtc_offer({"type": "offer", "sdp": "offer-sdp"})

        self.assertEqual(result, {"type": "answer", "sdp": "answer-sdp"})
        self.assertEqual(gateway.payloads, [{"type": "offer", "sdp": "offer-sdp"}])

    def test_session_webrtc_offer_returns_gateway_error_without_invalid_sdp(self) -> None:
        class FakeGateway:
            def offer(self, payload: dict[str, str]) -> dict[str, object]:
                return {"ok": False, "status": 501, "error": "Pion gateway implementation required"}

            def status(self) -> dict[str, object]:
                return {"running": True}

            def stop(self) -> None:
                return

        session = ShadowSession(ShadowConfig(), adb=AdbClient(ShadowConfig()), webrtc_gateway=FakeGateway())

        result = session.handle_webrtc_offer({"type": "offer", "sdp": "offer-sdp"})

        self.assertEqual(result["ok"], False)
        self.assertIn("Pion gateway", result["error"])
        self.assertNotIn("sdp", result)

    def test_session_status_includes_webrtc_state(self) -> None:
        class FakeGateway:
            def status(self) -> dict[str, object]:
                return {"running": True, "rtp_port": 9766}

            def stop(self) -> None:
                return

        session = ShadowSession(ShadowConfig(), adb=AdbClient(ShadowConfig()), webrtc_gateway=FakeGateway())
        session.screen_width = 1080
        session.screen_height = 2400

        status = session.status()

        self.assertEqual(status["video"]["backend"], "webrtc_h264")
        self.assertEqual(status["video"]["max_size"], 720)
        self.assertEqual(status["video"]["bitrate"], "2M")
        self.assertEqual(status["video"]["rtp_listen_host"], "0.0.0.0")
        self.assertEqual(status["webrtc_gateway"]["rtp_port"], 9766)

    def test_session_webrtc_prepare_runs_reachability_probe(self) -> None:
        class FakeAgent:
            def __init__(self) -> None:
                self.started = False
                self.stopped = False

            def validate_config(self) -> None:
                return

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

            def status(self) -> dict[str, object]:
                return {"running": self.started}

        class FakeGateway:
            def __init__(self) -> None:
                self.started = False
                self.stopped = False

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

            def status(self) -> dict[str, object]:
                return {"running": self.started}

        agent = FakeAgent()
        gateway = FakeGateway()
        result = ReachabilityResult(
            ok=True,
            probe="probe",
            target_host="192.168.1.20",
            target_port=9766,
            listen_host="0.0.0.0",
            elapsed_ms=3,
            received_from="192.168.1.30:40000",
            bytes_received=5,
            command="nc",
        )
        runner = RecordingRunner()
        config = ShadowConfig(android_agent_jar="/tmp/agent.jar", webrtc_transport="udp_rtp")
        adb = AdbClient(config, runner=runner)
        session = ShadowSession(config, adb=adb, android_agent=agent, webrtc_gateway=gateway)

        with (
            patch("nice_auther.shadow_root.session.check_android_udp_reachability", return_value=result) as probe,
            patch("nice_auther.shadow_root.session.log_reachability_result") as log_probe,
        ):
            session.prepare()

        probe.assert_called_once_with(adb, config)
        log_probe.assert_called_once_with(result)
        self.assertTrue(agent.started)
        self.assertTrue(gateway.started)
        self.assertEqual(session.status()["reachability"]["ok"], True)
        self.assertEqual(session.status()["reachability"]["target_host"], "192.168.1.20")

    def test_session_webrtc_tcp_transport_sets_up_adb_reverse(self) -> None:
        class FakeAgent:
            def __init__(self) -> None:
                self.started = False

            def validate_config(self) -> None:
                return

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                return

            def status(self) -> dict[str, object]:
                return {"running": self.started}

        class FakeGateway:
            def __init__(self) -> None:
                self.started = False

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                return

            def status(self) -> dict[str, object]:
                return {"running": self.started}

        runner = RecordingRunner()
        config = ShadowConfig(android_agent_jar="/tmp/agent.jar", port=8765, webrtc_transport="adb_reverse_tcp")
        adb = AdbClient(config, runner=runner)
        session = ShadowSession(config, adb=adb, android_agent=FakeAgent(), webrtc_gateway=FakeGateway())

        session.prepare()
        session.close()

        self.assertIn(["adb", "reverse", "tcp:9766", "tcp:9766"], runner.calls)
        self.assertIn(["adb", "reverse", "--remove", "tcp:9766"], runner.calls)

    def test_session_webrtc_tcp_direct_skips_adb_reverse(self) -> None:
        class FakeAgent:
            def validate_config(self) -> None:
                return

            def start(self) -> None:
                return

            def stop(self) -> None:
                return

            def status(self) -> dict[str, object]:
                return {"running": True}

        class FakeGateway:
            def start(self) -> None:
                return

            def stop(self) -> None:
                return

            def status(self) -> dict[str, object]:
                return {"running": True}

        runner = RecordingRunner()
        config = ShadowConfig(android_agent_jar="/tmp/agent.jar", port=8765, webrtc_transport="tcp_direct", webrtc_rtp_host="139.224.44.6")
        adb = AdbClient(config, runner=runner)
        session = ShadowSession(config, adb=adb, android_agent=FakeAgent(), webrtc_gateway=FakeGateway())

        session.prepare()

        self.assertNotIn(["adb", "reverse", "tcp:9766", "tcp:9766"], runner.calls)

    def test_reachability_log_outputs_json(self) -> None:
        result = ReachabilityResult(
            ok=False,
            probe="probe",
            target_host="127.0.0.1",
            target_port=9766,
            listen_host="0.0.0.0",
            elapsed_ms=1500,
            error="timeout waiting for Android UDP probe",
            command="nc",
        )

        with patch("sys.stdout", new_callable=StringIO) as stdout:
            log_reachability_result(result)

        payload = stdout.getvalue()
        self.assertIn('"component": "shadow_root.reachability"', payload)
        self.assertIn('"ok": false', payload)
        self.assertIn('"target_host": "127.0.0.1"', payload)

    def test_session_wake_display_sends_keyevents_and_agent_pli(self) -> None:
        runner = RecordingRunner()
        started: list[list[str]] = []
        config = ShadowConfig(port=8765)
        adb = AdbClient(config, runner=runner, popen_factory=lambda args, **kwargs: started.append(args) or FakeProcess())
        session = ShadowSession(config, adb=adb)

        result = session.wake_display()

        self.assertTrue(result["ok"])
        self.assertIn(["adb", "shell", "su", "-c", "input keyevent KEYCODE_WAKEUP"], runner.calls)
        self.assertIn(["adb", "shell", "su", "-c", "input keyevent KEYCODE_MENU"], runner.calls)
        self.assertTrue(any("printf PLI" in " ".join(call) and "9767" in " ".join(call) for call in started))

    def test_webrtc_missing_agent_jar_does_not_abort_http_session(self) -> None:
        runner = RecordingRunner()
        config = ShadowConfig()
        adb = AdbClient(config, runner=runner)
        session = ShadowSession(config, adb=adb)

        session.prepare()
        status = session.status()
        offer_result = session.handle_webrtc_offer({"type": "offer", "sdp": "offer-sdp"})

        self.assertIn("SHADOW_ANDROID_AGENT_JAR is required", status["webrtc_error"])
        self.assertEqual(offer_result["ok"], False)
        self.assertIn("SHADOW_ANDROID_AGENT_JAR is required", offer_result["error"])

    def test_web_ui_displays_webrtc_configuration_error(self) -> None:
        self.assertIn('data.webrtc_error', INDEX_HTML)
        self.assertIn('state.textContent = "webrtc config error"', INDEX_HTML)
        self.assertIn('WebRTC unavailable', INDEX_HTML)

    def test_android_agent_build_script_reports_missing_sdk(self) -> None:
        build_script = Path(__file__).resolve().parents[1] / "nice_auther" / "shadow_root" / "android_agent_project" / "build_android_agent.py"
        spec = importlib.util.spec_from_file_location("build_android_agent", build_script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict("os.environ", {"ANDROID_HOME": "", "ANDROID_SDK_ROOT": ""}, clear=False),
                patch.object(module.Path, "home", return_value=Path(tmp)),
                patch("sys.stderr", new_callable=StringIO) as stderr,
            ):
                result = module.main()

        self.assertEqual(result, 2)
        self.assertIn("ANDROID_HOME or ANDROID_SDK_ROOT is required", stderr.getvalue())

    def test_webrtc_gateway_build_script_exists(self) -> None:
        build_script = Path(__file__).resolve().parents[1] / "nice_auther" / "shadow_root" / "webrtc_gateway" / "build_gateway.py"

        self.assertTrue(build_script.exists())

    def test_android_agent_project_contains_real_rtp_sender_logic(self) -> None:
        root = Path(__file__).resolve().parents[1] / "nice_auther" / "shadow_root" / "android_agent_project" / "src" / "nice" / "auther" / "shadow"

        packetizer = (root / "H264RtpPacketizer.java").read_text()
        annexb = (root / "H264AnnexB.java").read_text()
        sender = (root / "UdpRtpSender.java").read_text()
        tcp_sender = (root / "TcpRtpSender.java").read_text()
        control = (root / "ControlServer.java").read_text()
        encoder = (root / "H264SurfaceEncoder.java").read_text()
        main = (root / "AgentMain.java").read_text()
        config = (root / "AgentConfig.java").read_text()
        mirror = (root / "DisplayMirror.java").read_text()

        self.assertIn("FU-A", packetizer)
        self.assertIn("nalSummary", packetizer)
        self.assertIn("splitAvccNalUnits", annexb)
        self.assertIn("concat(byte[] first, byte[] second)", annexb)
        self.assertIn("packet[1] = (byte) ((marker ? 0x80 : 0) | 96)", packetizer)
        self.assertIn("DatagramSocket", sender)
        self.assertIn("sendAnnexBFrame", sender)
        self.assertIn("new Socket(host, port)", tcp_sender)
        self.assertIn("setTcpNoDelay(true)", tcp_sender)
        self.assertIn("output.write((length >>> 8) & 0xff)", tcp_sender)
        self.assertIn("consumeIdrRequest", control)
        self.assertIn("MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface", encoder)
        self.assertIn("AVCProfileBaseline", encoder)
        self.assertIn("MediaFormat.KEY_LEVEL", encoder)
        self.assertIn("PARAMETER_KEY_REQUEST_SYNC_FRAME", encoder)
        self.assertIn("BUFFER_FLAG_CODEC_CONFIG", encoder)
        self.assertIn("frameSink.onFrame(frame, info.presentationTimeUs, true)", encoder)
        self.assertIn("--self-test-rtp", config)
        self.assertIn("--transport", config)
        self.assertIn("TcpRtpSender", main)
        self.assertIn("sendSelfTestFrame", main)
        self.assertIn("SurfaceControl", mirror)
        self.assertIn("DisplayManager", mirror)
        self.assertIn("createVirtualDisplay", mirror)
        self.assertIn("setDisplaySurface", mirror)
        self.assertIn("setDisplayProjection", mirror)
        self.assertIn("IDisplayManager$Stub", mirror)
        self.assertIn("DisplayMirror.create(inputSurface", main)


if __name__ == "__main__":
    unittest.main()
