from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import build_initial_message, build_initial_messages


class InitialMessageTests(unittest.TestCase):
    def test_build_initial_message_uses_provided_config_xml_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text(
                """
<package name="com.sankuai.meituan">
    <city name="安徽省宿州市">
        <args>
            <address name="万成哈佛国际A区"/>
            <range size="5000"/>
        </args>
        <subpage name="外卖">
            <leaf name="美食"/>
        </subpage>
    </city>
</package>
""",
                encoding="utf-8",
            )

            message = build_initial_message(
                workspace,
                current_date="2026-06-14",
                timezone="Asia/Shanghai",
            )

        self.assertIn("<environment_context>", message)
        self.assertIn(f"  <cwd>{workspace}</cwd>", message)
        self.assertIn("  <shell>zsh</shell>", message)
        self.assertIn("  <current_date>2026-06-14</current_date>", message)
        self.assertIn("  <timezone>Asia/Shanghai</timezone>", message)
        self.assertIn("- 应用包名：com.sankuai.meituan", message)
        self.assertIn("- 城市：安徽省宿州市", message)
        self.assertIn("- 地址：万成哈佛国际A区", message)
        self.assertIn("- 范围：5000米", message)
        self.assertIn("- subpage：外卖（当前页面内可点击跳转，路径：外卖）", message)
        self.assertIn("- leaf：美食（最终采集目标页面，路径：外卖 -> 美食）", message)
        self.assertNotIn("二级页面", message)
        self.assertNotIn("<page_mechanism_context>", message)

    def test_build_initial_message_tolerates_trailing_junk_after_xml_root(self) -> None:
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
        <subpage name="外卖">
            <leaf name="美食"/>
        </subpage>
    </city>
</package>aa
""",
                encoding="utf-8",
            )

            message = build_initial_message(
                workspace,
                current_date="2026-06-14",
                timezone="Asia/Shanghai",
            )

        self.assertIn("- 应用包名：com.sankuai.meituan", message)
        self.assertIn("- 城市：安徽省宿州市", message)
        self.assertIn("- 地址：万成哈弗国际A区1栋", message)
        self.assertIn("- 范围：1000米", message)
        self.assertIn("- subpage：外卖（当前页面内可点击跳转，路径：外卖）", message)
        self.assertIn("- leaf：美食（最终采集目标页面，路径：外卖 -> 美食）", message)

    def test_build_initial_message_supports_multiple_cities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text(
                """
<package name="com.sankuai.meituan">
    <city name="安徽省宿州市">
        <args>
            <address name="万成哈佛国际A区"/>
            <range size="5000"/>
        </args>
        <subpage name="外卖">
            <leaf name="美食"/>
        </subpage>
    </city>
    <city name="上海市">
        <args>
            <address name="人民广场"/>
            <range size="3km"/>
        </args>
        <subpage name="美食">
            <leaf name="火锅"/>
        </subpage>
    </city>
</package>
""",
                encoding="utf-8",
            )

            message = build_initial_message(
                workspace,
                current_date="2026-06-14",
                timezone="Asia/Shanghai",
            )

        self.assertIn("- 应用包名：com.sankuai.meituan", message)
        self.assertIn("- 城市：安徽省宿州市", message)
        self.assertIn("- 地址：万成哈佛国际A区", message)
        self.assertIn("- 范围：5000米", message)
        self.assertIn("- leaf：美食（最终采集目标页面，路径：外卖 -> 美食）", message)
        self.assertIn("- 城市：上海市", message)
        self.assertIn("- 地址：人民广场", message)
        self.assertIn("- 范围：3km", message)
        self.assertIn("- leaf：火锅（最终采集目标页面，路径：美食 -> 火锅）", message)

    def test_build_initial_message_keeps_nested_subpage_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text(
                """
<package name="com.sankuai.meituan">
    <city name="安徽省宿州市">
        <args>
            <address name="万成哈佛国际A区"/>
            <range size="5000"/>
        </args>
        <subpage name="外卖">
            <subpage name="频道">
                <leaf name="美食"/>
            </subpage>
        </subpage>
    </city>
</package>
""",
                encoding="utf-8",
            )

            message = build_initial_message(
                workspace,
                current_date="2026-06-14",
                timezone="Asia/Shanghai",
            )

        outer = message.index("- subpage：外卖（当前页面内可点击跳转，路径：外卖）")
        inner = message.index("- subpage：频道（当前页面内可点击跳转，路径：外卖 -> 频道）")
        leaf = message.index("- leaf：美食（最终采集目标页面，路径：外卖 -> 频道 -> 美食）")
        self.assertLess(outer, inner)
        self.assertLess(inner, leaf)

    def test_build_initial_message_does_not_parse_legacy_text_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text(
                """
<config>
  <应用名称>美团</应用名称>
  <城市>上海</城市>
  <采集参数>
    <地址>人民广场</地址>
    <范围>3km</范围>
  </采集参数>
  <二级页面1>美食</二级页面1>
  <二级页面2>甜品饮品</二级页面2>
</config>
""",
                encoding="utf-8",
            )

            message = build_initial_message(
                workspace,
                current_date="2026-06-14",
                timezone="Asia/Shanghai",
            )

        self.assertIn("- 应用包名：未配置", message)
        self.assertIn("- 城市：未配置", message)
        self.assertIn("- 地址：未配置", message)
        self.assertIn("- 范围：未配置", message)
        self.assertIn("- 页面任务树：未配置", message)
        self.assertNotIn("- 应用名称：美团", message)
        self.assertNotIn("- 二级页面1：美食", message)

    def test_build_initial_message_tolerates_empty_config_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text("", encoding="utf-8")

            message = build_initial_message(workspace, current_date="2026-06-14")

        self.assertIn("- 应用包名：未配置", message)
        self.assertIn("- 城市：未配置", message)
        self.assertIn("- 页面任务树：未配置", message)
        self.assertNotIn("No page mechanism documents found", message)

    def test_build_initial_messages_returns_only_environment_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text("<config></config>", encoding="utf-8")

            messages = build_initial_messages(workspace, current_date="2026-06-14")

        self.assertEqual(len(messages), 2)
        self.assertIn("<environment_context>", messages[0])
        self.assertIn("<config_context>", messages[1])


if __name__ == "__main__":
    unittest.main()
