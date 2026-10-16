"""Recorded gesture deployment and randomization, independent of Agent schemas."""

import random
import struct
from pathlib import Path
from posixpath import join
from tempfile import TemporaryDirectory
from typing import Final

from src.device.commands.executor import DEFAULT_ACTION_DIR, AndroidCommandExecutor
from src.device.names import ALL_ACTIONS, HOT_ACTIONS
from src.device.services.translator import build_replay_packet, normalize_events_to_origin, parse_action_log
from src.device.types.gestures import GestureSpec, ReplayTarget
from src.device.types.results import CommandResult, DeviceError, make_error_result

LOCAL_ACTION_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "actions"
GESTURES: Final[dict[str, GestureSpec]] = {
    name: GestureSpec(
        name, tuple(f"{name}_{i}" for i in range(3)) if name in HOT_ACTIONS else (name,), name in HOT_ACTIONS
    )
    for name in ALL_ACTIONS
}


class GestureReplay:
    def __init__(self, executor: AndroidCommandExecutor) -> None:
        self.executor: AndroidCommandExecutor = executor
        self._ready: bool = False

    def ensure_available(self) -> DeviceError | None:
        if self._ready:
            return None
        listed: CommandResult = self.executor.list_files(DEFAULT_ACTION_DIR)
        if listed.error is not None:
            return listed.error
        present: set[str] = set(listed.stdout.splitlines())
        try:
            for spec in GESTURES.values():
                for name in spec.recordings:
                    if name in present:
                        continue
                    source: Path = LOCAL_ACTION_DIR / name
                    packet: bytes = build_replay_packet(
                        normalize_events_to_origin(parse_action_log(source.read_text(encoding="utf-8")))
                    )
                    with TemporaryDirectory(prefix="bines-action-") as directory:
                        staged: Path = Path(directory) / name
                        staged.write_bytes(packet)
                        uploaded: CommandResult = self.executor.push_file(str(staged), DEFAULT_ACTION_DIR)
                    if uploaded.error is not None:
                        return uploaded.error
        except (OSError, ValueError, OverflowError, struct.error) as exc:
            return make_error_result("action_deployment_failed", str(exc))
        self._ready = True
        return None

    def replay(self, name: str, x: int, y: int) -> str | DeviceError:
        spec: GestureSpec | None = GESTURES.get(name)
        if spec is None:
            return make_error_result("validation_error", f"Unknown action: {name}")
        error: DeviceError | None = self.ensure_available()
        if error is not None:
            return error
        target: ReplayTarget = self._select(spec, x, y)
        result: CommandResult = self.executor.replay(target.path, target.x, target.y)
        return result.error if result.error is not None else result.stdout

    def _select(self, spec: GestureSpec, x: int, y: int) -> ReplayTarget:
        name: str = random.choice(spec.recordings) if spec.common else spec.recordings[0]
        if spec.common:
            x += random.randint(-2, 2)
            y += random.randint(-2, 2)
        return ReplayTarget(join(DEFAULT_ACTION_DIR, name), x, y)
