from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools.content_provider import ContentProvider, _read_xlsx


class ContentProviderTests(unittest.TestCase):
    def test_create_append_and_update_excel_file_under_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = ContentProvider(tmp_dir)
            created = Path(
                provider.create_excel_file(
                    "reports/stores.xlsx",
                    headers_json='["店名", "评分"]',
                    rows_json='[["A店", 4.8]]',
                )
            )
            provider.append_excel_rows("reports/stores.xlsx", '[["B店", 4.5]]')
            provider.update_excel_cell("reports/stores.xlsx", "C2", "已采集")

            self.assertEqual(created, Path(tmp_dir).resolve() / "reports/stores.xlsx")
            self.assertEqual(
                _read_xlsx(created),
                [
                    ["店名", "评分"],
                    ["A店", "4.8", "已采集"],
                    ["B店", "4.5"],
                ],
            )

    def test_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            provider = ContentProvider(tmp_dir)

            with self.assertRaises(ValueError):
                provider.create_excel_file("../outside.xlsx")


if __name__ == "__main__":
    unittest.main()
