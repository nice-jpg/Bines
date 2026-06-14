from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import build_initial_message


class InitialMessageTests(unittest.TestCase):
    def test_build_initial_message_uses_provided_config_xml_shape(self) -> None:
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
        <subpage name="外卖"/>
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
        self.assertIn("- 应用名称：com.sankuai.meituan", message)
        self.assertIn("- 城市：安徽省宿州市", message)
        self.assertIn("- 地址：万成哈弗国际A区1栋", message)
        self.assertIn("- 范围：1000米", message)
        self.assertIn("- 二级页面1：美食", message)
        self.assertIn("- 二级页面2：外卖", message)

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
        <subpage name="美食"/>
        <subpage name="外卖"/>
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

        self.assertIn("- 应用名称：com.sankuai.meituan", message)
        self.assertIn("- 城市：安徽省宿州市", message)
        self.assertIn("- 地址：万成哈弗国际A区1栋", message)
        self.assertIn("- 范围：1000米", message)
        self.assertIn("- 二级页面1：美食", message)
        self.assertIn("- 二级页面2：外卖", message)

    def test_build_initial_message_keeps_legacy_text_config_compatibility(self) -> None:
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

        self.assertIn("<environment_context>", message)
        self.assertIn(f"  <cwd>{workspace}</cwd>", message)
        self.assertIn("  <shell>zsh</shell>", message)
        self.assertIn("  <current_date>2026-06-14</current_date>", message)
        self.assertIn("  <timezone>Asia/Shanghai</timezone>", message)
        self.assertIn("- 应用名称：美团", message)
        self.assertIn("- 城市：上海", message)
        self.assertIn("- 地址：人民广场", message)
        self.assertIn("- 范围：3km", message)
        self.assertIn("- 二级页面1：美食", message)
        self.assertIn("- 二级页面2：甜品饮品", message)

    def test_build_initial_message_tolerates_empty_config_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            (workspace / "config.xml").write_text("", encoding="utf-8")

            message = build_initial_message(workspace, current_date="2026-06-14")

        self.assertIn("- 应用名称：未配置", message)
        self.assertIn("- 城市：未配置", message)
        self.assertIn("- 二级页面1：未配置", message)
        self.assertIn("- 二级页面2：未配置", message)


if __name__ == "__main__":
    unittest.main()
