import os
import tempfile
import unittest
from unittest.mock import patch

from mcp_server import MCPServer


ROOT = os.path.dirname(os.path.dirname(__file__))


class MCPServerTests(unittest.TestCase):
    def test_run_full_analysis_tool_core_path(self):
        server = MCPServer()
        with tempfile.TemporaryDirectory() as tmp:
            result = server._call_run_full_analysis(
                {
                    "poi_path": os.path.join(ROOT, "data", "poi_snapshot.csv"),
                    "context_path": os.path.join(ROOT, "data", "region_context.json"),
                    "dictionary_path": os.path.join(ROOT, "data", "industry_dictionary.json"),
                    "center_lat": 31.2304,
                    "center_lng": 121.4737,
                    "radius_km": 1.5,
                    "output_dir": tmp,
                },
                emit_notifications=False,
            )
            self.assertIn("top3", result)
            self.assertEqual(len(result["top3"]), 3)
            self.assertTrue(os.path.exists(os.path.join(tmp, "industry_scorecard.csv")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "top_opportunities.json")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "analysis_report.md")))

    @patch("mcp_server.acquire_market_inputs")
    def test_acquire_market_inputs_tool(self, mocked_acquire):
        mocked_acquire.return_value = {
            "region_query": "上海 徐家汇",
            "resolved_location": "Xujiahui, Shanghai, China",
            "center_lat": 31.2304,
            "center_lng": 121.4737,
            "radius_km": 1.4,
            "poi_count": 88,
            "poi_path": "/tmp/poi_snapshot.csv",
            "context_path": "/tmp/region_context.json",
        }
        server = MCPServer()
        result = server._call_acquire_market_inputs({"region_query": "上海 徐家汇", "output_dir": "/tmp"})
        self.assertIn("center_lat", result)
        self.assertEqual(result["poi_count"], 88)
        self.assertEqual(server.latest_inputs["radius_km"], 1.4)

    @patch("mcp_server.acquire_market_inputs")
    def test_auto_acquire_then_analyze(self, mocked_acquire):
        server = MCPServer()
        with tempfile.TemporaryDirectory() as tmp:
            mocked_acquire.return_value = {
                "region_query": "上海 徐家汇",
                "resolved_location": "Xujiahui, Shanghai, China",
                "center_lat": 31.2304,
                "center_lng": 121.4737,
                "radius_km": 1.5,
                "poi_count": 50,
                "poi_path": os.path.join(ROOT, "data", "poi_snapshot.csv"),
                "context_path": os.path.join(ROOT, "data", "region_context.json"),
            }
            acquired = server._call_acquire_market_inputs({"region_query": "上海 徐家汇", "output_dir": tmp})
            analysis = server._call_run_full_analysis(
                {
                    "poi_path": acquired["poi_path"],
                    "context_path": acquired["context_path"],
                    "dictionary_path": os.path.join(ROOT, "data", "industry_dictionary.json"),
                    "center_lat": acquired["center_lat"],
                    "center_lng": acquired["center_lng"],
                    "radius_km": acquired["radius_km"],
                    "output_dir": tmp,
                },
                emit_notifications=False,
            )
            self.assertIn("top3", analysis)
            self.assertEqual(len(analysis["top3"]), 3)


if __name__ == "__main__":
    unittest.main()
