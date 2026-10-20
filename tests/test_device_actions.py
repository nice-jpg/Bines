from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.device.types.results import DeviceError
from src.device.commands.executor import AndroidCommandExecutor as AndroidDevice
from src.device.types.results import CommandResult
from src.device.services.operations import DeviceOperationService
from src.device.services.page.bridge import PageBridge
from src.device.services.gestures import GestureReplay, GESTURES
from src.device.services.translator import (
    build_replay_packet,
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
    def test_pull_file_uses_adb_transfer_not_text_stdout(self):
        runner = RecordingRunner()
        device = AndroidDevice()
        device.serial = "test"
        device.runner = runner
        assert device.pull_file("/sdcard/screen.png", "/tmp/screen.png").ok
        assert runner.calls == [["adb", "-s", "test", "pull", "/sdcard/screen.png", "/tmp/screen.png"]]

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
        device = AndroidDevice()
        device.runner = runner

        self.assertEqual(device.list_files("/sdcard/Documents/actions/").stdout.splitlines(), ["touch", "act"])
        device.push_file("/tmp/touch", "/sdcard/Documents/actions/")
        device.replay("/sdcard/Documents/actions/touch", 7, -2)

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
        device = AndroidDevice()
        device.runner = runner

        self.assertTrue(device.capture_screen("/sdcard/window.png").ok)
        self.assertIn(["adb", "shell", "screencap", "-p", "/sdcard/window.png"], runner.calls)

    def test_android_device_dump_ui_disables_animations_before_uiautomator_dump(self) -> None:
        runner = RecordingRunner(
            {
                ("adb", "shell", "cat", "/sdcard/window_dump.xml"): (
                    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
                    "<hierarchy rotation=\"0\"></hierarchy>\n"
                ),
            }
        )
        device = AndroidDevice()
        device.runner = runner

        self.assertEqual(PageBridge(device)._dump_xml(), "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n<hierarchy rotation=\"0\"></hierarchy>")
        self.assertEqual(
            runner.calls[:4],
            [
                ["adb", "shell", "settings", "put", "global", "window_animation_scale", "0"],
                ["adb", "shell", "settings", "put", "global", "transition_animation_scale", "0"],
                ["adb", "shell", "settings", "put", "global", "animator_duration_scale", "0"],
                ["adb", "shell", "/data/local/tmp/project", "-d", "/sdcard/window_dump.xml"],
            ],
        )

    def test_android_device_run_package_uses_launcher_monkey_command(self) -> None:
        runner = RecordingRunner()
        device = AndroidDevice()
        device.runner = runner

        device.start_package("com.sankuai.meituan")

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

    def test_android_device_run_package_returns_error_for_empty_package_name(self) -> None:
        device = AndroidDevice()
        device.runner = RecordingRunner()

        result = DeviceOperationService().run_package("  ")

        self.assertIsInstance(result, DeviceError)
        self.assertEqual(result.error_type, "validation_error")
        self.assertIn("package_name must not be empty", result.message)

    def test_android_device_close_package_uses_force_stop_command(self) -> None:
        runner = RecordingRunner()
        device = AndroidDevice()
        device.runner = runner

        device.stop_package("com.sankuai.meituan")

        self.assertIn(
            ["adb", "shell", "am", "force-stop", "com.sankuai.meituan"],
            runner.calls,
        )

    def test_android_device_close_package_returns_error_for_empty_package_name(self) -> None:
        device = AndroidDevice()
        device.runner = RecordingRunner()

        result = DeviceOperationService().close_package("  ")

        self.assertIsInstance(result, DeviceError)
        self.assertEqual(result.error_type, "validation_error")
        self.assertIn("package_name must not be empty", result.message)

    def test_default_runner_returns_error_result_for_adb_error_output(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["adb", "shell", "some-command"],
            returncode=0,
            stdout="ERROR could not get idle state.\n",
            stderr="",
        )
        device = AndroidDevice()

        with patch("src.device.commands.executor.subprocess.run", return_value=completed):
            result = device.dump_hierarchy().error

        self.assertIsInstance(result, DeviceError)
        self.assertEqual(result.error_type, "command_error")
        self.assertIn("ERROR could not get idle state.", result.message)

    def test_default_runner_returns_error_result_for_stderr_error_output(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["adb", "shell", "some-command"],
            returncode=0,
            stdout="",
            stderr="Permission denied\n",
        )
        device = AndroidDevice()

        with patch("src.device.commands.executor.subprocess.run", return_value=completed):
            result = device.dump_hierarchy().error

        self.assertIsInstance(result, DeviceError)
        self.assertEqual(result.error_type, "command_error")
        self.assertIn("Permission denied", result.message)

    def test_default_runner_preserves_successful_output_containing_error_word_inside_text(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["adb", "shell", "some-command"],
            returncode=0,
            stdout="last_error_count=0\n",
            stderr="",
        )
        device = AndroidDevice()

        with patch("src.device.commands.executor.subprocess.run", return_value=completed):
            self.assertEqual(device.dump_hierarchy().stdout, "last_error_count=0\n")

    def test_deployment_uploads_real_missing_files_including_back(self):
        uploads = []
        present = {name for spec in GESTURES.values() for name in spec.recordings} - {"swipe_back"}
        def runner(args):
            if "push" in args:
                uploads.append((Path(args[-2]).name, Path(args[-2]).read_bytes()))
            return CommandResult(args, 0, "\n".join(present) if "ls" in args else "", "")
        device = AndroidDevice()
        device.runner = runner
        gestures = GestureReplay(device)
        assert gestures.ensure_available() is None
        assert [name for name, _ in uploads] == ["swipe_back"]
        assert uploads[0][1].startswith(b"PIAR1\0\0\0")
        assert gestures.ensure_available() is None
        assert len(uploads) == 1

    def test_failed_upload_is_not_cached(self):
        calls = []
        def runner(args):
            calls.append(args)
            return CommandResult(args, 1 if "push" in args else 0, "", "upload failed" if "push" in args else "")
        device = AndroidDevice()
        device.runner = runner
        gestures = GestureReplay(device)
        assert isinstance(gestures.ensure_available(), DeviceError)
        assert isinstance(gestures.ensure_available(), DeviceError)
        assert sum("push" in args for args in calls) == 2

    def test_custom_runner_errors_and_exceptions_are_normalized(self):
        device = AndroidDevice()
        device.runner = lambda args: CommandResult(args, 0, "", "Permission denied")
        assert device.dump_hierarchy().error.error_type == "command_error"
        def broken(args):
            raise OSError("missing adb")
        device.runner = broken
        result = device.dump_hierarchy()
        assert result.returncode is None and result.error is not None

    def test_randomization_is_confined_to_common_gestures(self):
        device = AndroidDevice()
        calls = []
        present = "\n".join(name for spec in GESTURES.values() for name in spec.recordings)
        def runner(args):
            calls.append(args)
            return CommandResult(args, 0, present if "ls" in args else "", "")
        device.runner = runner
        gestures = GestureReplay(device)
        with patch("src.device.services.gestures.random.choice", return_value="tap_2") as choice, patch("src.device.services.gestures.random.randint", side_effect=[-2, 2]) as jitter:
            assert gestures.replay("tap", 100, 200) == ""
            assert calls[-1][-1].endswith("/tap_2 98 202")
            assert gestures.replay("swipe_back", 0, 2000) == ""
            assert calls[-1][-1].endswith("/swipe_back 0 2000")
            assert choice.call_count == 1
            assert jitter.call_args_list == [unittest.mock.call(-2, 2), unittest.mock.call(-2, 2)]
