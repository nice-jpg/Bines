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

    def test_page_mechanism_dirs_include_task_and_run_py(self) -> None:
        expected_dirs = [
            Path("."),
            Path("美食"),
            Path("外卖"),
            Path("美食/商家"),
            Path("外卖/商家"),
        ]

        for relative_dir in expected_dirs:
            with self.subTest(relative_dir=relative_dir.as_posix()):
                page_dir = PAGE_ROOT / relative_dir
                self.assertTrue((page_dir / "PAGE.md").is_file())
                self.assertTrue((page_dir / "task.md").is_file())
                self.assertTrue((page_dir / "run.py").is_file())
                run_text = (page_dir / "run.py").read_text(encoding="utf-8")
                self.assertIn("def run(xml: str, previous: dict | None = None) -> dict", run_text)

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
                task = (PAGE_ROOT / relative_path).with_name("task.md").read_text(encoding="utf-8")
                self.assertIn("## Structure", content)
                self.assertIn("Do not directly extract product names", task)
                self.assertIn("page-local run.py JSON", task)


if __name__ == "__main__":
    unittest.main()
