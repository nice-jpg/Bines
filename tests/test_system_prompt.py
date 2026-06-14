from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import SYSTEM_PROMPT


class SystemPromptTests(unittest.TestCase):
    def test_prompt_requires_scrolling_list_pages_until_terminal_condition(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
