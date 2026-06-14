from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device.adapter import AndroidDevice, CommandResult
from device.translator import (
    build_replay_packet,
    check_actions,
    group_events_by_syn_report,
    normalize_events_to_origin,
    parse_action_log,
    parse_getevent_line,
)


class RecordingRunner:
    def __init__(self, outputs: dict[tuple[str, ...], str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.outputs = outputs or {}

    def __call__(self, args: list[str]) -> CommandResult:
        self.calls.append(args)
        return CommandResult(args=args, returncode=0, stdout=self.outputs.get(tuple(args), ""), stderr="")


class DeviceActionTests(unittest.TestCase):
    def test_parse_getevent_line_supports_symbolic_pathless_and_signed_hex(self) -> None:
        self.assertEqual(parse_getevent_line("[ 1.000000] EV_KEY BTN_TOUCH DOWN").value, 1)
        self.assertEqual(parse_getevent_line("[ 1.000000] EV_KEY BTN_TOUCH UP").value, 0)
        self.assertEqual(parse_getevent_line("[ 1.000000] EV_ABS ABS_MT_TRACKING_ID ffffffff").value, -1)

        event = parse_getevent_line("[ 1.000000] /dev/input/event2: 0003 0035 000003a1")
        self.assertIsNotNone(event)
        self.assertEqual(event.device_path, "/dev/input/event2")
        self.assertEqual(event.type, 3)
        self.assertEqual(event.code, 53)
        self.assertEqual(event.value, 929)

    def test_parse_action_log_preserves_delay_and_at_seconds(self) -> None:
        events = parse_action_log(
            """
[ 1.000000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000001
[ 1.025000] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 000003a1
[ 2.177000] /dev/input/event2: EV_SYN SYN_REPORT 00000000
"""
        )

        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].delay_ms, 0)
        self.assertEqual(events[1].delay_ms, 25)
        self.assertEqual(events[2].delay_ms, 1152)
        self.assertAlmostEqual(events[1].at_seconds, 0.025, places=6)

    def test_group_events_by_syn_report(self) -> None:
        events = parse_action_log(
            """
[ 1.000000] EV_ABS ABS_MT_POSITION_X 00000010
[ 1.000000] EV_SYN SYN_REPORT 00000000
[ 1.006000] EV_ABS ABS_MT_POSITION_X 00000011
[ 1.006000] EV_SYN SYN_REPORT 00000000
"""
        )
        frames = group_events_by_syn_report(events)

        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]["delay_seconds"], 0)
        self.assertAlmostEqual(frames[1]["delay_seconds"], 0.006, places=6)

    def test_normalize_events_to_origin_shifts_touch_coordinates_only(self) -> None:
        events = parse_action_log(
            """
[ 1.000000] EV_ABS ABS_MT_POSITION_X 00000064
[ 1.000000] EV_ABS ABS_MT_POSITION_Y 000000c8
[ 1.000000] EV_ABS ABS_MT_PRESSURE 00000024
[ 1.006000] EV_ABS ABS_MT_POSITION_X 0000006e
[ 1.006000] EV_ABS ABS_MT_POSITION_Y 000000be
"""
        )

        normalized = normalize_events_to_origin(events)

        self.assertEqual(normalized[0].value, 0)
        self.assertEqual(normalized[1].value, 0)
        self.assertEqual(normalized[2].value, 36)
        self.assertEqual(normalized[3].value, 10)
        self.assertEqual(normalized[4].value, -10)

    def test_build_replay_packet_matches_helper_format(self) -> None:
        events = parse_action_log(
            """
[ 1.000000] EV_KEY BTN_TOUCH DOWN
[ 1.000000] EV_SYN SYN_REPORT 00000000
[ 1.025000] EV_KEY BTN_TOUCH UP
[ 1.025000] EV_SYN SYN_REPORT 00000000
"""
        )
        packet = build_replay_packet(events)

        self.assertEqual(packet[:8], b"PIAR1\0\0\0")
        self.assertEqual(struct.unpack_from("<I", packet, 8)[0], 0)
        self.assertEqual(struct.unpack_from("<I", packet, 12)[0], 2)
        self.assertEqual(struct.unpack_from("<H", packet, 16)[0], 1)
        self.assertEqual(struct.unpack_from("<H", packet, 18)[0], 330)
        self.assertEqual(struct.unpack_from("<i", packet, 20)[0], 1)
        self.assertEqual(struct.unpack_from("<I", packet, 32)[0], 25000)
        self.assertEqual(struct.unpack_from("<I", packet, 36)[0], 2)

    def test_android_device_builds_adb_root_push_list_and_act_commands(self) -> None:
        runner = RecordingRunner(
            {
                ("adb", "shell", "ls", "-1", "/sdcard/Documents/actions/"): "touch\nact\n",
            }
        )
        device = AndroidDevice(runner=runner)

        self.assertEqual(device.get_supported_actions(), ["touch", "act"])
        device.push_file("/tmp/touch", "/sdcard/Documents/actions/")
        device.act("touch", (7, -2))

        self.assertIn(["adb", "shell", "mkdir", "-p", "/sdcard/Documents/actions/"], runner.calls)
        self.assertIn(["adb", "push", "/tmp/touch", "/sdcard/Documents/actions/touch"], runner.calls)
        self.assertIn(
            [
                "adb",
                "shell",
                "su",
                "-c",
                "/data/local/tmp/pi_input_replay /dev/input/event3 /sdcard/Documents/actions/touch 7 -2",
            ],
            runner.calls,
        )

    def test_android_device_screenshot_captures_remote_png_path(self) -> None:
        runner = RecordingRunner()
        device = AndroidDevice(runner=runner)

        self.assertEqual(device.screenshot(), "/sdcard/window.png")
        self.assertIn(["adb", "shell", "screencap", "-p", "/sdcard/window.png"], runner.calls)

    def test_android_device_run_package_uses_launcher_monkey_command(self) -> None:
        runner = RecordingRunner()
        device = AndroidDevice(runner=runner)

        device.run_package("com.sankuai.meituan")

        self.assertIn(
            [
                "adb",
                "shell",
                "monkey",
                "-p",
                "com.sankuai.meituan",
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ],
            runner.calls,
        )

    def test_android_device_run_package_rejects_empty_package_name(self) -> None:
        device = AndroidDevice(runner=RecordingRunner())

        with self.assertRaises(ValueError):
            device.run_package("  ")

    def test_check_actions_translates_missing_local_actions(self) -> None:
        class FakeDevice:
            def __init__(self) -> None:
                self.added: list[tuple[str, str]] = []

            def get_supported_actions(self, device_dir: str = "/sdcard/Documents/actions/") -> list[str]:
                return ["already"]

            def add_action(self, local_path: str, device_dir: str = "/sdcard/Documents/actions/") -> str:
                self.added.append((Path(local_path).name, device_dir))
                return f"{device_dir.rstrip('/')}/{Path(local_path).name}"

        with tempfile.TemporaryDirectory() as tmp_dir:
            local_dir = Path(tmp_dir)
            (local_dir / "already").write_text("[ 1.000000] EV_SYN SYN_REPORT 00000000\n", encoding="utf-8")
            (local_dir / "missing").write_text("[ 1.000000] EV_SYN SYN_REPORT 00000000\n", encoding="utf-8")

            device = FakeDevice()
            translated = check_actions(device, local_dir=str(local_dir))

        self.assertEqual(translated, ["missing"])
        self.assertEqual(len(device.added), 1)
        self.assertEqual(device.added[0][0], "missing")


if __name__ == "__main__":
    unittest.main()
