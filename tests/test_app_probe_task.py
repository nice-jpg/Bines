from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tasks.app_probe import (
    APP_PROBE_TRIGGER,
    build_app_probe_messages,
    is_app_probe_request,
    messages_request_app_probe,
)


class AppProbeTaskTests(unittest.TestCase):
    def test_app_probe_trigger_requires_exact_match(self) -> None:
        self.assertTrue(is_app_probe_request(APP_PROBE_TRIGGER))
        self.assertFalse(is_app_probe_request(f" {APP_PROBE_TRIGGER}"))
        self.assertFalse(is_app_probe_request(f"{APP_PROBE_TRIGGER} "))
        self.assertFalse(is_app_probe_request(f"{APP_PROBE_TRIGGER}。"))
        self.assertFalse(is_app_probe_request("普通聊天"))

    def test_messages_request_app_probe_supports_plain_and_feishu_context(self) -> None:
        self.assertTrue(messages_request_app_probe([{"role": "user", "content": APP_PROBE_TRIGGER}]))
        self.assertTrue(
            messages_request_app_probe(
                [{"role": "user", "content": f"<feishu_message><text>{APP_PROBE_TRIGGER}</text></feishu_message>"}]
            )
        )
        self.assertFalse(
            messages_request_app_probe(
                [{"role": "user", "content": f"<feishu_message><text>{APP_PROBE_TRIGGER} </text></feishu_message>"}]
            )
        )

    def test_build_app_probe_messages_wraps_existing_initial_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text(
                """
<package name="com.sankuai.meituan">
    <city name="安徽省宿州市">
        <args>
            <address name="万成哈弗国际A区1栋"/>
            <range size="1000"/>
        </args>
        <subpage name="美食"/>
    </city>
</package>
""",
                encoding="utf-8",
            )

            messages = build_app_probe_messages(workspace, current_date="2026-06-22")

        self.assertEqual(len(messages), 2)
        self.assertIn("<environment_context>", messages[0]["content"])
        self.assertIn("<config_context>", messages[1]["content"])
        self.assertIn("- 应用名称：com.sankuai.meituan", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
