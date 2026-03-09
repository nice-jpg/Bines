import os
import unittest

from market_analysis import (
    INDUSTRIES,
    assign_industry,
    compute_scores,
    coverage_ratio,
    deduplicate_poi,
    filter_region,
    load_dictionary,
    load_poi,
    load_region_context,
    run_outlier_robustness,
    run_weight_sensitivity,
)


ROOT = os.path.dirname(os.path.dirname(__file__))
POI_PATH = os.path.join(ROOT, "data", "poi_snapshot.csv")
CTX_PATH = os.path.join(ROOT, "data", "region_context.json")
DICT_PATH = os.path.join(ROOT, "data", "industry_dictionary.json")


class MarketAnalysisTests(unittest.TestCase):
    def _prepare(self):
        poi = load_poi(POI_PATH)
        dct = load_dictionary(DICT_PATH)
        ctx = load_region_context(CTX_PATH)
        poi = deduplicate_poi(poi)
        poi = filter_region(poi, 31.2304, 121.4737, 1.5)
        poi = assign_industry(poi, dct)
        return poi, ctx

    def test_data_coverage_above_80_percent(self):
        poi, _ = self._prepare()
        ratio = coverage_ratio(poi)
        self.assertGreaterEqual(ratio, 0.8)

    def test_scorecard_has_all_industries(self):
        poi, ctx = self._prepare()
        scores = compute_scores(poi, ctx)
        names = [s.industry for s in scores]
        self.assertEqual(set(names), set(INDUSTRIES))

    def test_weight_sensitivity_top3_mostly_stable(self):
        poi, ctx = self._prepare()
        result = run_weight_sensitivity(poi, ctx)
        overlaps = list(result["overlap_with_variants"].values())
        self.assertTrue(all(v >= 2 for v in overlaps))

    def test_outlier_robustness(self):
        poi, ctx = self._prepare()
        result = run_outlier_robustness(poi, ctx)
        self.assertGreaterEqual(result["removed_outliers"], 1)
        self.assertGreaterEqual(result["overlap"], 2)


if __name__ == "__main__":
    unittest.main()
