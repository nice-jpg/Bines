from __future__ import annotations

import inspect
import shlex
from unittest.mock import patch
from src.device.types.results import CommandResult, DeviceError
from src.device.services.gestures import GESTURES
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.device.types.results import make_error_result
from src.device.services.operations import DeviceOperationService
from src.tools.device.operations import create_event_hub_tools
from src.tools.shared.safety import DANGEROUS_TOOL_POLICY_METADATA_KEY, DangerousToolPolicy


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

    def __call__(self, args):
        output = ""
        error = None
        if "ls" in args:
            output = "\n".join(name for spec in GESTURES.values() for name in spec.recordings)
        elif "su" in args:
            command = shlex.split(args[-1])
            name = Path(command[2]).name
            if name[-2:] in {"_0", "_1", "_2"}:
                name = name[:-2]
            self.calls.append(("act", name, (int(command[3]), int(command[4]))))
            if isinstance(self.act_result, DeviceError): error = self.act_result
        elif "cat" in args:
            self.calls.append(("dump_ui",))
            output = self.ui_result
        elif "force-stop" in args:
            self.calls.append(("close_package", args[-1]))
        return CommandResult(args, 1 if error else 0, output, "", error)


class EventHubTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("src.device.services.gestures.random.randint", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_device_tools_have_explicit_typed_policy_and_coordinate_schema(self) -> None:
        tools = {tool.name: tool for tool in create_event_hub_tools()}
        for name in ("tap", "swipe_up", "swipe_up_long", "swipe_down"):
            self.assertEqual(set(tools[name].args), {"x", "y"})
        for name in ("run_package", "close_package"):
            policy = tools[name].metadata[DANGEROUS_TOOL_POLICY_METADATA_KEY]
            self.assertIsInstance(policy, DangerousToolPolicy)
            self.assertEqual(set(tools[name].args), {"package_name", "justification"})

    def test_touch_action_returns_uiautomate_result_after_success(self) -> None:
        logger = FakeLogger()
        sleeper = FakeSleeper()
        hub = DeviceOperationService()
        hub.logger = logger
        hub.page_bridge._logger = logger
        hub._sleep = sleeper
        hub.executor.runner = FakeDevice(ui_result='<hierarchy><node text="验证码 优惠券" bounds="[0,0][10,10]" /></hierarchy>')

        result = hub.tap(11, 22)

        self.assertIn("验证码", result[0]["summary"])
        self.assertIn("page_xml", result[1])
        self.assertEqual(sleeper.calls, [1.2])
        self.assertEqual(hub.executor.runner.calls, [("act", "tap", (11, 22)), ("dump_ui",)])
        self.assertIn(("captcha", "Detected captcha signal in UI hierarchy", {"source": "page_view"}), logger.records)
        self.assertIn(("popup", "Detected popup signal in UI hierarchy", {"source": "page_view"}), logger.records)

    def test_touch_action_returns_and_logs_error_result_after_failure(self) -> None:
        logger = FakeLogger()
        sleeper = FakeSleeper()
        hub = DeviceOperationService()
        hub.logger = logger
        hub.page_bridge._logger = logger
        hub._sleep = sleeper
        error = make_error_result("command_error", "adb failed", details={"args": ["adb"]})
        hub.executor.runner = FakeDevice(act_result=error)

        result = hub.swipe_up(10, 100)

        self.assertEqual(result, (error.as_dict(), None))
        self.assertEqual(sleeper.calls, [])
        self.assertEqual(hub.executor.runner.calls, [("act", "swipe_up", (10, 100))])
        self.assertEqual(logger.records, [("command_error", "adb failed", {"args": ["adb"]})])

    def test_new_swipe_actions_use_recorded_action_replay(self) -> None:
        hub = DeviceOperationService()
        hub._sleep = lambda _seconds: None
        hub.executor.runner = FakeDevice()

        hub.swipe_up_long(20, 1800)
        hub.swipe_to_random(30, 1600)

        self.assertEqual(
            hub.executor.runner.calls,
            [
                ("act", "swipe_up_long", (20, 1800)),
                ("dump_ui",),
                ("act", "swipe_to_random", (30, 1600)),
                ("dump_ui",),
            ],
        )

    def test_page_view_does_not_sleep_when_called_directly(self) -> None:
        sleeper = FakeSleeper()
        hub = DeviceOperationService()
        hub._sleep = sleeper
        hub.executor.runner = FakeDevice(ui_result='<hierarchy><node text="hello"/></hierarchy>')

        result, artifact = hub.page_bridge.call("page_view", {})

        self.assertEqual(result["summary"], "hello")
        self.assertIn("page_xml", artifact)
        self.assertEqual(sleeper.calls, [])
        self.assertEqual(hub.executor.runner.calls, [("dump_ui",)])

    def test_touch_action_returns_region_view_without_collection_wrapper(self) -> None:
        sleeper = FakeSleeper()
        hub = DeviceOperationService()
        hub._sleep = sleeper
        hub.executor.runner = FakeDevice(ui_result='<hierarchy><node text="hello"/></hierarchy>')

        direct = hub.page_bridge.call("page_view", {})
        swiped = hub.swipe_up(10, 100)

        self.assertEqual(swiped[0], {"kind": "page_change_hint", "view_update_ratio": 0.0, "node_update_ratio": 0.0, "regions": []})
        self.assertNotIn("xml", swiped[0])

    def test_close_package_calls_device_without_ui_capture(self) -> None:
        hub = DeviceOperationService()
        hub.executor.runner = FakeDevice()

        result = hub.close_package("com.sankuai.meituan")

        self.assertEqual(result, "")
        self.assertEqual(hub.executor.runner.calls, [("close_package", "com.sankuai.meituan")])

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
            list(inspect.signature(DeviceOperationService.run_package).parameters),
            ["self", "package_name"],
        )


if __name__ == "__main__":
    unittest.main()
