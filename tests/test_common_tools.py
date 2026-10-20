from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.tools.captcha.tools import CaptchaAuthenticationTool, create_captcha_tools
from src.tools.notifications.service import OperationNoticeTool
from src.tools.reasoning.tools import ThinkingTool, create_thinking_tool



class FakeLogger:
    def __init__(self) -> None:
        self.records = []

    def log(self, category, message, details=None) -> None:
        self.records.append((category, message, details or {}))


class FakeShadowService:
    def __init__(self) -> None:
        self.calls = []

    def start_shadow_service(self):
        self.calls.append("start")
        return {"ok": True, "running": True}

    def stop_shadow_service(self):
        self.calls.append("stop")
        return {"ok": True, "running": False}


class CommonToolTests(unittest.TestCase):
    def _notice_tool(self, *, logger, max_entries=30, notifier=None):
        from unittest.mock import patch
        with patch("src.tools.notifications.service.OPERATION_LOG_CAPACITY", max_entries):
            tool = OperationNoticeTool(notifier=notifier)
        tool.logger = logger
        return tool


    def _captcha_tool(self, *, service_module=None, **kwargs):
        from unittest.mock import patch
        if service_module is not None:
            start = patch("nScreen.shadow_root.start_shadow_service", service_module.start_shadow_service)
            stop = patch("nScreen.shadow_root.stop_shadow_service", service_module.stop_shadow_service)
            start.start()
            stop.start()
            self.addCleanup(start.stop)
            self.addCleanup(stop.stop)
        logger = kwargs.pop("logger", None)
        tool = CaptchaAuthenticationTool(**kwargs)
        if logger is not None:
            tool.logger = logger
        return tool

    def test_notify_user_returns_operation_log_and_persists_notice(self) -> None:
        logger = FakeLogger()
        result = self._notice_tool(logger=logger).notify_user(
            operation="swipe up on (200, 1200) to load more shops",
            current_path="meituan/外卖",
            reason="current list screen has no unprocessed visible shops",
        )

        self.assertIn("<operation_log>", result)
        self.assertIn("swipe up on (200, 1200) to load more shops", result)
        self.assertIn('current_path="meituan/外卖"', result)
        self.assertIn("current list screen has no unprocessed visible shops", result)
        self.assertEqual(logger.records[0][0], "operation_notice")
        self.assertEqual(logger.records[0][1], "swipe up on (200, 1200) to load more shops")
        self.assertEqual(logger.records[0][2]["current_path"], "meituan/外卖")

    def test_notify_user_calls_optional_notifier_without_changing_result(self) -> None:
        delivered = []
        result = self._notice_tool(
            logger=FakeLogger(),
            notifier=delivered.append,
        ).notify_user(
            operation="tap '美食' on (320, 620) to load a subpage",
            current_path="meituan",
            reason="open configured secondary page",
            next_page="美食",
            next_path="meituan/美食",
        )

        self.assertIn("<operation_log>", result)
        self.assertEqual(len(delivered), 1)
        self.assertIn("Preparing operation: tap '美食' on (320, 620) to load a subpage", delivered[0])
        self.assertIn("Next path: meituan/美食", delivered[0])

    def test_notify_user_records_expected_next_page_context(self) -> None:
        result = self._notice_tool(logger=FakeLogger()).notify_user(
            operation="tap '美食' on (320, 620) to load a subpage",
            current_path="meituan",
            reason="open configured secondary page",
            next_page="美食",
            next_path="meituan/美食",
        )

        self.assertIn('next_page="美食"', result)
        self.assertIn('next_path="meituan/美食"', result)

    def test_notify_user_keeps_recent_log_tail(self) -> None:
        tool = self._notice_tool(logger=FakeLogger(), max_entries=2)

        tool.notify_user("first operation", "meituan")
        tool.notify_user("second operation", "meituan")
        result = tool.notify_user("third operation", "meituan")

        self.assertNotIn("first operation", result)
        self.assertIn("second operation", result)
        self.assertIn("third operation", result)
        self.assertIn('count="2" max="2"', result)

    def test_notify_user_handles_empty_operation_without_raising(self) -> None:
        self.assertEqual(
            self._notice_tool(logger=FakeLogger()).notify_user("  ", "meituan"),
            "No operation notice was recorded because the operation input was empty.",
        )

    def test_think_records_non_empty_thought_without_side_effect_output(self) -> None:
        self.assertEqual(ThinkingTool().think("XML shows three shops; next scroll."), "Thought recorded.")

    def test_think_handles_empty_input(self) -> None:
        self.assertEqual(ThinkingTool().think("  "), "No thought was recorded because the input was empty.")

    def test_authenticate_captcha_starts_service_and_notifies_user(self) -> None:
        logger = FakeLogger()
        delivered = []
        service = FakeShadowService()

        result = self._captcha_tool(
            logger=logger,
            notifier=delivered.append,
            service_module=service,
        ).authenticate_captcha(
            current_path="meituan/外卖",
            reason="security check blocks the shop list",
            evidence="XML contains 验证码",
        )

        self.assertEqual(service.calls, ["start"])
        self.assertIn("<shadow_service", result)
        self.assertIn('operation="start"', result)
        self.assertIn("http://139.224.44.6:9080", result)
        self.assertEqual(logger.records[0][0], "captcha_authentication")
        self.assertEqual(logger.records[0][2]["current_path"], "meituan/外卖")
        self.assertEqual(logger.records[0][2]["reason"], "security check blocks the shop list")
        self.assertEqual(logger.records[1][0], "shadow_service_start")
        self.assertEqual(len(delivered), 1)
        self.assertIn("http://139.224.44.6:9080", delivered[0])

    def test_authenticate_captcha_handles_empty_inputs_without_raising(self) -> None:
        result = self._captcha_tool(
            logger=FakeLogger(),
            service_module=FakeShadowService(),
        ).authenticate_captcha("", "", "")

        self.assertIn('operation="start"', result)

    def test_start_shadow_service_starts_service_and_notifies_user(self) -> None:
        logger = FakeLogger()
        delivered = []
        service = FakeShadowService()

        result = self._captcha_tool(
            logger=logger,
            notifier=delivered.append,
            service_module=service,
        ).start_authentication_session()

        self.assertEqual(service.calls, ["start"])
        self.assertIn('operation="start"', result)
        self.assertIn("http://139.224.44.6:9080", result)
        self.assertEqual(logger.records[0][0], "shadow_service_start")
        self.assertEqual(len(delivered), 1)
        self.assertIn("http://139.224.44.6:9080", delivered[0])
        self.assertIn("done", delivered[0])

    def test_stop_shadow_service_stops_service(self) -> None:
        logger = FakeLogger()
        service = FakeShadowService()

        result = self._captcha_tool(logger=logger, service_module=service).stop_authentication_session()

        self.assertEqual(service.calls, ["stop"])
        self.assertIn('operation="stop"', result)
        self.assertEqual(logger.records[0][0], "shadow_service_stop")

    def test_captcha_authenticated_stops_service(self) -> None:
        logger = FakeLogger()
        service = FakeShadowService()

        result = self._captcha_tool(logger=logger, service_module=service).captcha_authenticated(
            current_path="meituan/外卖",
            evidence="user replied done",
        )

        self.assertEqual(service.calls, ["stop"])
        self.assertIn('operation="stop"', result)
        self.assertEqual(logger.records[0][0], "captcha_authenticated")
        self.assertEqual(logger.records[0][2]["current_path"], "meituan/外卖")
        self.assertEqual(logger.records[1][0], "shadow_service_stop")

    def test_create_common_tools_registers_think_tool(self) -> None:
        tools = [*create_captcha_tools(CaptchaAuthenticationTool()), create_thinking_tool()]

        self.assertEqual(
            [tool.name for tool in tools],
            [
                "authenticate_captcha",
                "captcha_authenticated",
                "think",
            ],
        )
        self.assertIn("human captcha authentication", tools[0].description)
        self.assertIn("captcha has been completed", tools[1].description)
        self.assertIn("complex tool outputs", tools[2].description)
        self.assertIn("does not fetch new information", tools[2].description)
