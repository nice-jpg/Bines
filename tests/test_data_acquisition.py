import unittest

from data_acquisition import (
    _select_best_geocode_candidate,
    build_poi_snapshot,
    build_region_context,
    suggest_radius_km,
)


class DataAcquisitionTests(unittest.TestCase):
    def _sample_elements(self):
        return [
            {
                "type": "node",
                "id": 1,
                "lat": 31.2305,
                "lon": 121.4739,
                "tags": {"amenity": "cafe", "name": "Cafe One", "brand": "X", "opening_hours": "Mo-Su 08:00-22:00"},
            },
            {
                "type": "node",
                "id": 2,
                "lat": 31.2307,
                "lon": 121.4734,
                "tags": {"shop": "convenience", "name": "Mini Mart"},
            },
            {
                "type": "node",
                "id": 3,
                "lat": 31.2299,
                "lon": 121.4742,
                "tags": {"shop": "hairdresser", "name": "Hair Lab"},
            },
            {
                "type": "node",
                "id": 4,
                "lat": 31.2302,
                "lon": 121.4729,
                "tags": {"building": "residential"},
            },
            {
                "type": "node",
                "id": 5,
                "lat": 31.2312,
                "lon": 121.4731,
                "tags": {"office": "company"},
            },
            {
                "type": "node",
                "id": 6,
                "lat": 31.2301,
                "lon": 121.4732,
                "tags": {"highway": "bus_stop"},
            },
        ]

    def test_build_poi_snapshot(self):
        rows = build_poi_snapshot(self._sample_elements(), 31.2304, 121.4737, 1.5)
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(all("category_l2" in r for r in rows))
        self.assertTrue(all(r["open_status"] in ("open", "closed") for r in rows))

    def test_suggest_radius(self):
        poi_rows = [
            {"lat": 31.2304 + i * 0.00015, "lng": 121.4737 + i * 0.00008}
            for i in range(1, 30)
        ]
        radius = suggest_radius_km(31.2304, 121.4737, poi_rows, 1.5)
        self.assertGreaterEqual(radius, 0.8)
        self.assertLessEqual(radius, 2.0)

    def test_build_region_context(self):
        poi_rows = build_poi_snapshot(self._sample_elements(), 31.2304, 121.4737, 1.5)
        ctx = build_region_context(self._sample_elements(), poi_rows)
        self.assertIn("day_night_population_proxy", ctx)
        self.assertIn("office_residential_ratio", ctx)
        self.assertIn("accessibility_proxy", ctx)
        self.assertIn("rent_proxy", ctx)

    def test_geocode_candidate_selection_prefers_exact_city(self):
        candidates = [
            {
                "display_name": "合肥市, 安徽省, 中国",
                "lat": "31.8206",
                "lon": "117.2272",
                "type": "city",
                "importance": 0.7,
                "name": "合肥市",
            },
            {
                "display_name": "宿州市, 安徽省, 中国",
                "lat": "33.6482",
                "lon": "116.9588",
                "type": "city",
                "importance": 0.6,
                "name": "宿州市",
            },
        ]
        best = _select_best_geocode_candidate("安徽省宿州市", candidates)
        self.assertIn("宿州市", best["display_name"])


if __name__ == "__main__":
    unittest.main()
