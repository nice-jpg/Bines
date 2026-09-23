from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from device.results import make_error_result
from tools.event_hub import EventHub, create_event_hub_tools
from tools.tool_decorators import dangerous_policy_for, is_xy_operation


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

    def close_package(self, package_name: str):
        self.calls.append(("close_package", package_name))
        return ""


class EventHubTests(unittest.TestCase):
    def test_event_hub_methods_declare_tool_behavior_with_decorators(self) -> None:
        self.assertTrue(is_xy_operation(EventHub.tap))
        self.assertTrue(is_xy_operation(EventHub.swipe_up))
        self.assertTrue(is_xy_operation(EventHub.swipe_up_long))
        self.assertTrue(is_xy_operation(EventHub.swipe_to_random))
        self.assertTrue(is_xy_operation(EventHub.swipe_down))
        self.assertIsNotNone(dangerous_policy_for(EventHub.run_package))
        self.assertIsNotNone(dangerous_policy_for(EventHub.close_package))
        self.assertFalse(hasattr(EventHub, "uiautomate"))

    def test_touch_action_returns_uiautomate_result_after_success(self) -> None:
        logger = FakeLogger()
        sleeper = FakeSleeper()
        hub = EventHub(logger=logger, sleep_func=sleeper)
        hub.device = FakeDevice(ui_result='<hierarchy><node text="验证码 优惠券" bounds="[0,0][10,10]" /></hierarchy>')
        hub._actions_checked = True

        result = hub.tap(11, 22)

        self.assertIn("验证码", result[0]["summary"])
        self.assertIn("page_xml", result[1])
        self.assertEqual(sleeper.calls, [1.2])
        self.assertEqual(hub.device.calls, [("act", "tap", (11, 22)), ("dump_ui",)])
        self.assertIn(("captcha", "Detected captcha signal in UI hierarchy", {"source": "page_view"}), logger.records)
        self.assertIn(("popup", "Detected popup signal in UI hierarchy", {"source": "page_view"}), logger.records)

    def test_touch_action_returns_and_logs_error_result_after_failure(self) -> None:
        logger = FakeLogger()
        sleeper = FakeSleeper()
        hub = EventHub(logger=logger, sleep_func=sleeper)
        error = make_error_result("command_error", "adb failed", details={"args": ["adb"]})
        hub.device = FakeDevice(act_result=error)
        hub._actions_checked = True

        result = hub.swipe_up(10, 100)

        self.assertEqual(result, (error, None))
        self.assertEqual(sleeper.calls, [])
        self.assertEqual(hub.device.calls, [("act", "swipe_up", (10, 100))])
        self.assertEqual(logger.records, [("command_error", "adb failed", {"args": ["adb"]})])

    def test_new_swipe_actions_use_recorded_action_replay(self) -> None:
        hub = EventHub(sleep_func=lambda _seconds: None)
        hub.device = FakeDevice()
        hub._actions_checked = True

        hub.swipe_up_long(20, 1800)
        hub.swipe_to_random(30, 1600)

        self.assertEqual(
            hub.device.calls,
            [
                ("act", "swipe_up_long", (20, 1800)),
                ("dump_ui",),
                ("act", "swipe_to_random", (30, 1600)),
                ("dump_ui",),
            ],
        )

    def test_page_view_does_not_sleep_when_called_directly(self) -> None:
        sleeper = FakeSleeper()
        hub = EventHub(sleep_func=sleeper)
        hub.device = FakeDevice(ui_result='<hierarchy><node text="hello"/></hierarchy>')

        result, artifact = hub.page_bridge.call("page_view", {})

        self.assertEqual(result["summary"], "hello")
        self.assertIn("page_xml", artifact)
        self.assertEqual(sleeper.calls, [])
        self.assertEqual(hub.device.calls, [("dump_ui",)])

    def test_touch_action_returns_region_view_without_collection_wrapper(self) -> None:
        sleeper = FakeSleeper()
        hub = EventHub(sleep_func=sleeper)
        hub.device = FakeDevice(ui_result='<hierarchy><node text="hello"/></hierarchy>')
        hub._actions_checked = True

        direct = hub.page_bridge.call("page_view", {})
        swiped = hub.swipe_up(10, 100)

        self.assertEqual(direct[0], swiped[0])
        self.assertNotIn("xml", swiped[0])

    def test_close_package_calls_device_without_ui_capture(self) -> None:
        hub = EventHub()
        hub.device = FakeDevice()

        result = hub.close_package("com.sankuai.meituan")

        self.assertEqual(result, "")
        self.assertEqual(hub.device.calls, [("close_package", "com.sankuai.meituan")])

    def test_dangerous_tool_wrapper_consumes_justification_before_event_hub(self) -> None:
        calls = []
        fake_hub = type(
            "FakeHub",
            (),
            {"run_package": lambda _self, package_name: calls.append(package_name) or "started"},
        )()
        tool = create_event_hub_tools(fake_hub, tool_names=("run_package",))[0]

        result = tool.invoke(
            {
                "package_name": "com.sankuai.meituan",
                "justification": {
                    "reason": "Run the configured probe.",
                    "evidence": "The requested application is not running.",
                    "expected_outcome": "The home page opens.",
                    "risk_acknowledged": True,
                },
            }
        )

        self.assertEqual(result, "started")
        self.assertEqual(calls, ["com.sankuai.meituan"])
        self.assertEqual(
            list(inspect.signature(EventHub.run_package).parameters),
            ["self", "package_name"],
        )


if __name__ == "__main__":
    unittest.main()
