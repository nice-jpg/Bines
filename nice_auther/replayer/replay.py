"""Replay execution for nice_auther bundles."""

from __future__ import annotations

from pathlib import Path
import base64
import tempfile
from typing import Any

from . import imports as _imports  # noqa: F401 - installs src on sys.path.
from .bundle import ReplayBundle, coerce_bundle
from device.adapter import AndroidDevice
from device.results import ErrorResult, is_error_result
from device.translator import build_replay_packet, parse_action_log


DEFAULT_REMOTE_PACKET_DIR = "/data/local/tmp/nice_auther_replays"
DEFAULT_REPLAY_HELPER = "/data/local/tmp/pi_input_replay"


def replay_bundle(
    bundle: ReplayBundle | dict[str, Any] | str | Path,
    *,
    device: AndroidDevice | None = None,
    remote_packet_dir: str = DEFAULT_REMOTE_PACKET_DIR,
    replay_helper: str = DEFAULT_REPLAY_HELPER,
) -> str | ErrorResult:
    replay = coerce_bundle(bundle)
    packet = packet_from_bundle(replay)
    android = device or AndroidDevice()

    with tempfile.TemporaryDirectory(prefix="nice-auther-replay-") as tmp_dir:
        packet_path = Path(tmp_dir) / "recording.piar"
        packet_path.write_bytes(packet)
        remote_path = android.push_file(str(packet_path), remote_packet_dir)
        if is_error_result(remote_path):
            return remote_path

    return android.execute_file(
        replay_helper,
        [replay.input_device, str(remote_path), "0", "0"],
        root=True,
    )


def bundle_to_packet(bundle: ReplayBundle | dict[str, Any] | str | Path) -> bytes:
    return packet_from_bundle(coerce_bundle(bundle))


def packet_from_bundle(replay: ReplayBundle) -> bytes:
    if replay.piar_base64:
        return base64.b64decode(replay.piar_base64)
    events = parse_action_log(replay.raw_getevent_log)
    if not events:
        raise ValueError("raw_getevent_log did not contain replayable getevent events")
    return build_replay_packet(events)

