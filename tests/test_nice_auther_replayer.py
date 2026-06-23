from __future__ import annotations

import base64
import struct
import tempfile
import unittest
from pathlib import Path

from nice_auther.replayer import ReplayBundle, bundle_to_packet, replay_bundle


class FakeDevice:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def push_file(self, local_path: str, device_dir: str) -> str:
        self.calls.append(("push_file", (Path(local_path).name, device_dir, Path(local_path).read_bytes()[:8])))
        return f"{device_dir.rstrip('/')}/recording.piar"

    def execute_file(self, device_file_path: str, args: list[str] | None = None, root: bool = False) -> str:
        self.calls.append(("execute_file", (device_file_path, args, root)))
        return "ok"


def sample_bundle() -> dict[str, object]:
    return {
        "schema_version": 1,
        "created_at": "2026-06-22T00:00:00+00:00",
        "input_device": "/dev/input/event3",
        "screen": {"width": 1080, "height": 2400},
        "device_info": {"model": "test"},
        "raw_getevent_log": """
[ 1.000000] /dev/input/event3: EV_KEY BTN_TOUCH DOWN
[ 1.000000] /dev/input/event3: EV_SYN SYN_REPORT 00000000
[ 1.010000] /dev/input/event3: EV_KEY BTN_TOUCH UP
[ 1.010000] /dev/input/event3: EV_SYN SYN_REPORT 00000000
""",
        "raw_browser_events": [],
        "operations": [],
    }


class NiceAutherReplayerTests(unittest.TestCase):
    def test_replay_bundle_pushes_piar_and_executes_helper_without_delta(self) -> None:
        device = FakeDevice()

        result = replay_bundle(sample_bundle(), device=device)

        self.assertEqual(result, "ok")
        self.assertEqual(device.calls[0][0], "push_file")
        self.assertEqual(device.calls[0][1][2], b"PIAR1\0\0\0")
        self.assertEqual(
            device.calls[1],
            (
                "execute_file",
                (
                    "/data/local/tmp/pi_input_replay",
                    ["/dev/input/event3", "/data/local/tmp/nice_auther_replays/recording.piar", "0", "0"],
                    True,
                ),
            ),
        )

    def test_bundle_validation_requires_core_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            ReplayBundle.from_dict({"schema_version": 1})

    def test_bundle_to_packet_accepts_piar_base64_without_raw_log(self) -> None:
        packet = b"PIAR1\0\0\0" + struct.pack("<II", 0, 0)
        bundle = sample_bundle()
        bundle["raw_getevent_log"] = ""
        bundle["piar_base64"] = base64.b64encode(packet).decode("ascii")

        self.assertEqual(bundle_to_packet(bundle), packet)

    def test_replay_bundle_loads_from_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "bundle.json"
            path.write_text(__import__("json").dumps(sample_bundle()), encoding="utf-8")
            device = FakeDevice()

            self.assertEqual(replay_bundle(path, device=device), "ok")


if __name__ == "__main__":
    unittest.main()
