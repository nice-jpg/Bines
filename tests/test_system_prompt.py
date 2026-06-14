from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import SYSTEM_PROMPT


class SystemPromptTests(unittest.TestCase):
    def test_prompt_requires_scrolling_list_pages_until_terminal_condition(self) -> None:
        self.assertIn("run_package：按应用包名打开指定应用", SYSTEM_PROMPT)
        self.assertIn("调用 swipe_up 上滑加载下一屏", SYSTEM_PROMPT)
        self.assertIn("连续 2 次上滑后 XML/商家集合没有新增", SYSTEM_PROMPT)
        self.assertIn("不允许因为首屏商家少", SYSTEM_PROMPT)
        self.assertIn("出现范围外商家就直接返回", SYSTEM_PROMPT)

    def test_prompt_requires_range_filtering_and_detail_scanning(self) -> None:
        self.assertIn("未确认范围生效前不得开始批量采集", SYSTEM_PROMPT)
        self.assertIn("1000 表示 1000 米", SYSTEM_PROMPT)
        self.assertIn("500m、1.2km、约800米", SYSTEM_PROMPT)
        self.assertIn("没有距离信息的商家不能直接丢弃", SYSTEM_PROMPT)
        self.assertIn("商品列表执行滚动扫描", SYSTEM_PROMPT)
        self.assertIn("全部评价数量、好评数量", SYSTEM_PROMPT)
        self.assertIn("立即以店铺为单位整理采集结果并追加写入 Excel", SYSTEM_PROMPT)

    def test_prompt_describes_page_specific_operation_strategy(self) -> None:
        self.assertIn("优先使用 run_package 工具", SYSTEM_PROMPT)
        self.assertIn("不得使用 y=0 或 y=2400", SYSTEM_PROMPT)
        self.assertIn("一级页面操作逻辑：直接查找并点击上下文指定的二级页面入口", SYSTEM_PROMPT)
        self.assertIn("从上到下依次为搜索框、金刚区、商家列表", SYSTEM_PROMPT)
        self.assertIn("优先在商家列表区域执行上下滑动", SYSTEM_PROMPT)
        self.assertIn("距离合适则点击进入商家", SYSTEM_PROMPT)
        self.assertIn("上半部分为商家基本信息", SYSTEM_PROMPT)
        self.assertIn("左侧是可单独上下滑动的品类列表", SYSTEM_PROMPT)
        self.assertIn("右侧是该品类下的商品列表", SYSTEM_PROMPT)
        self.assertIn("通过 swipe_back 工具返回上一页", SYSTEM_PROMPT)
        self.assertIn("不确定下一步如何操作时，调用 screenshot", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
