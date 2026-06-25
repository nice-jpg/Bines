from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nScreen.shadow_root import ShadowConfig
from nScreen.shadow_root import __all__ as shadow_root_exports
from nScreen.shadow_root import service


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 12345
        self.returncode: int | None = None
        self.stdout = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeAdbClient:
    instances: list["FakeAdbClient"] = []

    def __init__(self, config: ShadowConfig) -> None:
        self.config = config
        self.push_calls: list[tuple[str, str]] = []
        self.shell_calls: list[tuple[list[str], bool]] = []
        self.kill_calls: list[tuple[str, bool]] = []
        self.start_calls: list[tuple[str, str, list[str]]] = []
        self.process = FakeProcess()
        FakeAdbClient.instances.append(self)

    def push_file(self, local_path: str, remote_path: str) -> str:
        self.push_calls.append((local_path, remote_path))
        return remote_path

    def shell(self, command: list[str], *, root: bool = False) -> str:
        self.shell_calls.append((command, root))
        return ""

    def kill_processes_matching(self, pattern: str, *, root: bool = True) -> None:
        self.kill_calls.append((pattern, root))

    def start_android_agent(self, remote_jar: str, main_class: str, args: list[str]) -> FakeProcess:
        self.start_calls.append((remote_jar, main_class, args))
        return self.process


class ShadowServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        service._AGENT = None
        FakeAdbClient.instances = []
        self.tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        service._AGENT = None
        self.tmp_dir.cleanup()

    def test_start_shadow_service_is_nonblocking_and_idempotent(self) -> None:
        config = self._make_config()
        with patch.object(service, "AdbClient", FakeAdbClient):
            first = service.start_shadow_service(config)
            second = service.start_shadow_service(config)

        self.assertTrue(first["ok"])
        self.assertTrue(first["running"])
        self.assertTrue(second["running"])
        self.assertEqual(len(FakeAdbClient.instances), 1)
        self.assertEqual(len(FakeAdbClient.instances[0].start_calls), 1)

    def test_stop_shadow_service_stops_agent_and_kills_device_process(self) -> None:
        config = self._make_config()
        with patch.object(service, "AdbClient", FakeAdbClient):
            service.start_shadow_service(config)
            result = service.stop_shadow_service()

        adb = FakeAdbClient.instances[0]
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["running"], False)
        self.assertEqual(adb.kill_calls[-1], (config.android_agent_main_class, True))
        self.assertTrue(adb.process.terminated)

    def test_stop_shadow_service_without_running_agent_is_idempotent(self) -> None:
        self.assertEqual(service.stop_shadow_service(), {"ok": True, "running": False})

    def test_shadow_root_exports_service_switch_functions(self) -> None:
        self.assertIn("start_shadow_service", shadow_root_exports)
        self.assertIn("stop_shadow_service", shadow_root_exports)

    def _make_config(self) -> ShadowConfig:
        path = Path(self.tmp_dir.name) / "agent.jar"
        path.write_bytes(b"jar")
        return ShadowConfig(android_agent_jar=str(path), webrtc_rtp_host="127.0.0.1")


if __name__ == "__main__":
    unittest.main()
