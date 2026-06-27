from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = ROOT / "src" / "prompts" / "page_mechanism" / "meituan"


class PageMechanismDocTests(unittest.TestCase):
    def test_meituan_page_docs_include_canonical_paths(self) -> None:
        expected = {
            "PAGE.md": "Current page: `meituan`.",
            "美食/PAGE.md": "Current page: `meituan/美食`.",
            "外卖/PAGE.md": "Current page: `meituan/外卖`.",
            "美食/商家/PAGE.md": "Current page: `meituan/美食/商家`.",
            "外卖/商家/PAGE.md": "Current page: `meituan/外卖/商家`.",
        }

        for relative_path, marker in expected.items():
            with self.subTest(relative_path=relative_path):
                content = (PAGE_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("## Canonical Manual Path", content)
                self.assertIn(marker, content)

    def test_secondary_page_docs_point_to_generic_merchant_manuals(self) -> None:
        food = (PAGE_ROOT / "美食" / "PAGE.md").read_text(encoding="utf-8")
        takeout = (PAGE_ROOT / "外卖" / "PAGE.md").read_text(encoding="utf-8")

        self.assertIn("use `meituan/美食/商家` as the next manual path", food)
        self.assertIn("use `meituan/外卖/商家` as the next manual path", takeout)
        self.assertIn("Do not use a concrete merchant name", food)
        self.assertIn("Do not use a concrete merchant name", takeout)

    def test_merchant_docs_require_complete_collection_before_returning(self) -> None:
        for relative_path in ["美食/商家/PAGE.md", "外卖/商家/PAGE.md"]:
            with self.subTest(relative_path=relative_path):
                content = (PAGE_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("Collect merchant fields: name, rating, sales, distance", content)
                self.assertIn("total review count, and positive review count", content)
                self.assertIn("Collect every reachable product: product name, price, and sales", content)
                self.assertIn("Do not leave this merchant until all merchant fields are resolved", content)
                self.assertIn("Append this merchant's rows to Excel before using `swipe_back`", content)


if __name__ == "__main__":
    unittest.main()
