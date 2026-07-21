from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = ROOT / "src" / "prompts" / "page_mechanism" / "meituan"


class PageMechanismDocTests(unittest.TestCase):
    def test_meituan_page_docs_include_canonical_paths(self) -> None:
        for page_doc in sorted(PAGE_ROOT.rglob("PAGE.md")):
            relative_path = page_doc.relative_to(PAGE_ROOT)
            with self.subTest(relative_path=relative_path):
                content = page_doc.read_text(encoding="utf-8")
                self.assertIn("## Canonical Manual Path", content)
                self.assertIn("Current page: `meituan", content)

    def test_page_mechanism_dirs_include_task_and_run_agent_py(self) -> None:
        page_dirs = sorted(page_doc.parent for page_doc in PAGE_ROOT.rglob("PAGE.md"))
        self.assertTrue(page_dirs)
        for page_dir in page_dirs:
            relative_dir = page_dir.relative_to(PAGE_ROOT)
            with self.subTest(relative_dir=relative_dir.as_posix()):
                self.assertTrue((page_dir / "task.md").is_file())
                self.assertTrue((page_dir / "run.agent.py").is_file())
                run_text = (page_dir / "run.agent.py").read_text(encoding="utf-8")
                self.assertIn("def run(xml: str) -> dict", run_text)

    def test_secondary_page_docs_point_to_generic_merchant_manuals(self) -> None:
        food = (PAGE_ROOT / "美食" / "PAGE.md").read_text(encoding="utf-8")
        takeout = (PAGE_ROOT / "外卖" / "PAGE.md").read_text(encoding="utf-8")

        self.assertIn("use `meituan/美食/商家` as the next manual path", food)
        self.assertIn("Do not use a concrete merchant name", food)
        self.assertIn("## Structure", takeout)

    def test_merchant_docs_keep_collection_schema_out_of_task_doc(self) -> None:
        merchant_docs = sorted(PAGE_ROOT.rglob("商家/PAGE.md"))
        self.assertTrue(merchant_docs)
        for page_doc in merchant_docs:
            relative_path = page_doc.relative_to(PAGE_ROOT)
            with self.subTest(relative_path=relative_path):
                content = page_doc.read_text(encoding="utf-8")
                task = page_doc.with_name("task.md").read_text(encoding="utf-8")
                run_text = page_doc.with_name("run.agent.py").read_text(encoding="utf-8")
                self.assertIn("## Structure", content)
                self.assertIn("## Collection Target", content)
                self.assertIn("## Merchant Schema", content)
                self.assertIn("## Collection Schema", content)
                self.assertNotIn("run.agent.py", task)
                self.assertNotIn("Merchant Schema", task)
                self.assertNotIn("Collection Schema", task)
                self.assertNotIn("Collection Target", task)
                self.assertNotIn('"merchant"', run_text)
                self.assertIn('return {"items":', run_text)
                self.assertIn("without calling `is_done` after ordinary swipes", task)
                self.assertIn("Call `is_done` only when the page may be exhausted", task)
                self.assertIn("three consecutive valid collection swipes", task)
                self.assertIn("only collection completion authorization", task)
                self.assertNotIn("two consecutive", task)


if __name__ == "__main__":
    unittest.main()
