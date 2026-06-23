from __future__ import annotations

import subprocess
import sys
import unittest
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch

from nice_auther.shadow_root import AdbClient, ShadowConfig, ShadowSession
from nice_auther.shadow_root.display import MjpegScreencapStreamer, create_display_streamer
from nice_auther.shadow_root.input_stream import ABS_MT_POSITION_X, ABS_MT_POSITION_Y, PIAR_MAGIC, TouchEventEncoder, parse_input_capabilities
from nice_auther.shadow_root.run import _config_from_args
from nice_auther.shadow_root.server import _ShadowHandler, _access_url_for_config, _bind_host_for_config, start_shadow_session
from nice_auther.shadow_root.tunnel import SshReverseTunnel, tunnel_access_url


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
        self.assertEqual(ShadowConfig.from_env({"SHADOW_VIDEO_FPS": "12"}).video_fps, 12)
        quality_config = ShadowConfig.from_env(
            {
                "SHADOW_VIDEO_FORMAT": "png",
                "SHADOW_VIDEO_QUALITY": "70",
                "SHADOW_VIDEO_SCALE": "0.4",
            }
        )
        self.assertEqual(quality_config.video_format, "png")
        self.assertEqual(quality_config.video_quality, 70)
        self.assertEqual(quality_config.video_scale, 0.4)
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
        config = _config_from_args(
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
        config = ShadowConfig(input_device="/dev/input/event3")
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
        self.assertTrue(process.stdin.getvalue().startswith(PIAR_MAGIC))
        self.assertNotIn(["adb", "shell", "input", "tap", "108", "240"], runner.calls)
        self.assertEqual(session.operations[-1]["type"], "pointerup")
        self.assertTrue(session.build_bundle()["piar_base64"])
        session.stop_recording()

    def test_frame_png_uses_adb_exec_out(self) -> None:
        runner = RecordingBinaryRunner()
        config = ShadowConfig()
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

        self.assertIsNone(handler._write_body(b"frame"))

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
        config = ShadowConfig(video_fps=30)
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

        config = ShadowConfig(video_format="jpeg", video_quality=30, video_scale=0.5, video_fps=30)
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

    def test_scrcpy_backend_currently_falls_back_to_mjpeg(self) -> None:
        config = ShadowConfig(video_backend="scrcpy_h264")
        adb = AdbClient(config, binary_runner=RecordingBinaryRunner())

        self.assertIsInstance(create_display_streamer(adb, config), MjpegScreencapStreamer)


if __name__ == "__main__":
    unittest.main()
