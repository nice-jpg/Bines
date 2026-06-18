from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if "langchain_core.tools" not in sys.modules:
    langchain_core = types.ModuleType("langchain_core")
    langchain_core_tools = types.ModuleType("langchain_core.tools")

    class StructuredTool:
        @classmethod
        def from_function(cls, **kwargs):
            instance = cls()
            instance.name = kwargs["name"]
            instance.description = kwargs["description"]
            instance.func = kwargs["func"]
            return instance

    langchain_core_tools.StructuredTool = StructuredTool
    sys.modules["langchain_core"] = langchain_core
    sys.modules["langchain_core.tools"] = langchain_core_tools

from device.results import make_error_result
from tools.event_hub import EventHub


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def log(self, category: str, message: str, details: dict | None = None) -> None:
        self.records.append((category, message, details or {}))


class FakeSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FakeDevice:
    def __init__(self, act_result="", ui_result="<hierarchy></hierarchy>") -> None:
        self.act_result = act_result
        self.ui_result = ui_result
        self.calls: list[tuple] = []

    def act(self, action_name: str, xy: tuple[int, int]):
        self.calls.append(("act", action_name, xy))
        return self.act_result

    def dump_ui(self):
        self.calls.append(("dump_ui",))
        return self.ui_result


class EventHubTests(unittest.TestCase):
    def test_touch_action_returns_uiautomate_result_after_success(self) -> None:
        logger = FakeLogger()
        sleeper = FakeSleeper()
        hub = EventHub(logger=logger, sleep_func=sleeper)
        hub.device = FakeDevice(ui_result='<hierarchy><node text="验证码 优惠券" bounds="[0,0][10,10]" /></hierarchy>')
        hub._actions_checked = True

        result = hub.tap(11, 22)

        self.assertIn("验证码", result)
        self.assertEqual(sleeper.calls, [1.2])
        self.assertEqual(hub.device.calls, [("act", "tap", (11, 22)), ("dump_ui",)])
        self.assertIn(("captcha", "Detected captcha signal in UI hierarchy", {"source": "uiautomate"}), logger.records)
        self.assertIn(("popup", "Detected popup signal in UI hierarchy", {"source": "uiautomate"}), logger.records)

    def test_touch_action_returns_and_logs_error_result_after_failure(self) -> None:
        logger = FakeLogger()
        sleeper = FakeSleeper()
        hub = EventHub(logger=logger, sleep_func=sleeper)
        error = make_error_result("command_error", "adb failed", details={"args": ["adb"]})
        hub.device = FakeDevice(act_result=error)
        hub._actions_checked = True

        result = hub.swipe_up(10, 100)

        self.assertEqual(result, error)
        self.assertEqual(sleeper.calls, [])
        self.assertEqual(hub.device.calls, [("act", "swipe_up", (10, 100))])
        self.assertEqual(logger.records, [("command_error", "adb failed", {"args": ["adb"]})])

    def test_uiautomate_does_not_sleep_when_called_directly(self) -> None:
        sleeper = FakeSleeper()
        hub = EventHub(sleep_func=sleeper)
        hub.device = FakeDevice(ui_result="<hierarchy></hierarchy>")

        result = hub.uiautomate()

        self.assertEqual(result, "<h></h>")
        self.assertEqual(sleeper.calls, [])
        self.assertEqual(hub.device.calls, [("dump_ui",)])


if __name__ == "__main__":
    unittest.main()
