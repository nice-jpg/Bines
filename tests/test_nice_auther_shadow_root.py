from __future__ import annotations

import subprocess
import sys
import unittest
from io import BytesIO
from unittest.mock import patch

from nice_auther.shadow_root import AdbClient, ShadowConfig, ShadowSession
from nice_auther.shadow_root.server import _ShadowHandler


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
        self.stdout = FakeStdout()
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


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
                "SHADOW_PORT": "9000",
                "SHADOW_ADB_PATH": "/opt/adb",
                "SHADOW_ADB_SERIAL": "device-1",
                "SHADOW_INPUT_DEVICE": "/dev/input/event9",
                "SHADOW_FRAME_INTERVAL_MS": "250",
                "SHADOW_TOKEN": "secret",
            }
        )

        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 9000)
        self.assertEqual(config.adb_path, "/opt/adb")
        self.assertEqual(config.adb_serial, "device-1")
        self.assertEqual(config.input_device, "/dev/input/event9")
        self.assertEqual(config.frame_interval_ms, 250)
        self.assertEqual(config.token, "secret")

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

    def test_pointer_events_inject_tap_and_record_audit(self) -> None:
        runner = RecordingRunner()
        config = ShadowConfig()
        adb = AdbClient(config, runner=runner, popen_factory=lambda *args, **kwargs: FakeProcess())
        session = ShadowSession(config, adb=adb)
        session.screen_width = 1080
        session.screen_height = 2400
        session.start_recording()

        session.handle_pointer_event({"type": "pointerdown", "pointer_id": 1, "x": 10, "y": 10, "width": 100, "height": 100})
        result = session.handle_pointer_event({"type": "pointerup", "pointer_id": 1, "x": 10, "y": 10, "width": 100, "height": 100})

        self.assertEqual(result["action"], "tap")
        self.assertIn(["adb", "shell", "input", "tap", "108", "240"], runner.calls)
        self.assertEqual(session.operations[-1]["action"], "tap")
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


if __name__ == "__main__":
    unittest.main()
