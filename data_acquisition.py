#!/usr/bin/env python3
import argparse
import csv
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from statistics import median
from typing import Callable, Dict, List, Optional, Tuple


DEFAULT_RADIUS_KM = 1.5
MIN_RADIUS_KM = 0.8
MAX_RADIUS_KM = 2.0


PRICE_BAND_BY_L2 = {
    "中式快餐": 2,
    "咖啡馆": 3,
    "面包甜点": 2,
    "便利店": 1,
    "服饰店": 3,
    "美妆集合店": 3,
    "理发店": 2,
    "美甲店": 2,
    "洗衣店": 2,
    "编程培训": 4,
    "语言培训": 4,
    "素质教育": 4,
    "口腔门诊": 4,
    "康复理疗": 3,
    "健康管理": 3,
    "桌游馆": 3,
    "剧本杀": 3,
    "健身工作室": 3,
}


TAG_TO_L2 = {
    ("amenity", "fast_food"): "中式快餐",
    ("amenity", "restaurant"): "中式快餐",
    ("amenity", "cafe"): "咖啡馆",
    ("shop", "bakery"): "面包甜点",
    ("shop", "convenience"): "便利店",
    ("shop", "clothes"): "服饰店",
    ("shop", "beauty"): "美妆集合店",
    ("shop", "cosmetics"): "美妆集合店",
    ("shop", "hairdresser"): "理发店",
    ("shop", "laundry"): "洗衣店",
    ("amenity", "language_school"): "语言培训",
    ("amenity", "school"): "素质教育",
    ("amenity", "college"): "素质教育",
    ("amenity", "dentist"): "口腔门诊",
    ("amenity", "clinic"): "健康管理",
    ("healthcare", "rehabilitation"): "康复理疗",
    ("leisure", "sports_centre"): "健身工作室",
    ("leisure", "fitness_centre"): "健身工作室",
    ("leisure", "escape_game"): "剧本杀",
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 25) -> Dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _http_post_json(url: str, data: str, headers: Optional[Dict[str, str]] = None, timeout: int = 45) -> Dict:
    req = urllib.request.Request(
        url,
        method="POST",
        data=data.encode("utf-8"),
        headers=headers or {"Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


@dataclass
class RegionPoint:
    display_name: str
    lat: float
    lng: float


def geocode_region(query: str, countrycodes: Optional[str] = None) -> RegionPoint:
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 8,
    }
    if countrycodes:
        params["countrycodes"] = countrycodes
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
    payload = _http_get_json(
        url,
        headers={
            "User-Agent": "market-analysis-mcp/0.1 (contact: local-agent)",
            "Accept": "application/json",
        },
    )
    if not payload:
        raise ValueError(f"无法解析地理位置: {query}")
    top = _select_best_geocode_candidate(query, payload)
    return RegionPoint(display_name=top["display_name"], lat=float(top["lat"]), lng=float(top["lon"]))


def _normalize_geo_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _extract_query_tokens(query: str) -> List[str]:
    normalized = _normalize_geo_text(query)
    # 按中文行政后缀切分，保留有意义的词片段。
    chunks = re.split(r"[省市区县州盟旗,\s]+", normalized)
    return [c for c in chunks if c]


def _select_best_geocode_candidate(query: str, candidates: List[Dict]) -> Dict:
    normalized_query = _normalize_geo_text(query)
    tokens = _extract_query_tokens(query)
    best = None
    best_score = -1.0
    for item in candidates:
        display = _normalize_geo_text(item.get("display_name", ""))
        score = 0.0
        # 完整串命中优先级最高，避免被同省其他城市抢占。
        if normalized_query and normalized_query in display:
            score += 10.0
        # 分词命中，提升“安徽+宿州”这类请求稳定性。
        token_hits = sum(1 for t in tokens if t and t in display)
        score += token_hits * 2.0
        # 类型偏好：city/town 比 province/state 更接近商圈分析入口。
        place_type = str(item.get("type", "")).lower()
        if place_type in {"city", "town", "administrative"}:
            score += 1.0
        # 名称字段也参与匹配，兼容 display_name 顺序差异。
        name = _normalize_geo_text(str(item.get("name", "")))
        if name and any(t in name for t in tokens):
            score += 1.5
        # 打平时优先 importance 高的结果。
        score += float(item.get("importance", 0.0)) * 0.01

        if score > best_score:
            best = item
            best_score = score
    return best if best is not None else candidates[0]


def build_overpass_query(lat: float, lng: float, radius_m: int) -> str:
    return f"""
[out:json][timeout:45];
(
  node(around:{radius_m},{lat},{lng})["amenity"];
  way(around:{radius_m},{lat},{lng})["amenity"];
  relation(around:{radius_m},{lat},{lng})["amenity"];
  node(around:{radius_m},{lat},{lng})["shop"];
  way(around:{radius_m},{lat},{lng})["shop"];
  relation(around:{radius_m},{lat},{lng})["shop"];
  node(around:{radius_m},{lat},{lng})["leisure"];
  way(around:{radius_m},{lat},{lng})["leisure"];
  relation(around:{radius_m},{lat},{lng})["leisure"];
  node(around:{radius_m},{lat},{lng})["office"];
  way(around:{radius_m},{lat},{lng})["office"];
  relation(around:{radius_m},{lat},{lng})["office"];
  node(around:{radius_m},{lat},{lng})["building"];
  way(around:{radius_m},{lat},{lng})["building"];
  relation(around:{radius_m},{lat},{lng})["building"];
  node(around:{radius_m},{lat},{lng})["public_transport"];
  node(around:{radius_m},{lat},{lng})["railway"="station"];
  node(around:{radius_m},{lat},{lng})["highway"="bus_stop"];
);
out center tags;
"""


def fetch_overpass(lat: float, lng: float, radius_km: float) -> List[Dict]:
    query = build_overpass_query(lat, lng, int(radius_km * 1000))
    payload = _http_post_json("https://overpass-api.de/api/interpreter", query)
    return payload.get("elements", [])


def _get_lat_lng(element: Dict) -> Optional[Tuple[float, float]]:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def _infer_category_l2(tags: Dict[str, str]) -> Optional[str]:
    keys = [("amenity", tags.get("amenity")), ("shop", tags.get("shop")), ("leisure", tags.get("leisure"))]
    for key in keys:
        if key in TAG_TO_L2:
            return TAG_TO_L2[key]
    amenity = tags.get("amenity", "")
    if "school" in amenity:
        return "素质教育"
    if tags.get("healthcare"):
        return "健康管理"
    if tags.get("office"):
        return "健康管理"
    return None


def _infer_open_status(tags: Dict[str, str]) -> str:
    if tags.get("disused") == "yes" or tags.get("abandoned") == "yes":
        return "closed"
    return "open"


def _infer_popularity(tags: Dict[str, str]) -> int:
    score = 10
    if tags.get("brand"):
        score += 15
    if tags.get("opening_hours"):
        score += 10
    if tags.get("website") or tags.get("contact:website"):
        score += 10
    if tags.get("phone") or tags.get("contact:phone"):
        score += 8
    if tags.get("addr:housenumber"):
        score += 5
    return int(clamp(score * 2.2, 8, 220))


def _infer_rating(tags: Dict[str, str], category_l2: str) -> float:
    base = 4.1
    if category_l2 in ("咖啡馆", "理发店", "口腔门诊"):
        base += 0.15
    if tags.get("brand"):
        base += 0.1
    if tags.get("opening_hours"):
        base += 0.05
    return round(clamp(base, 3.7, 4.8), 2)


def build_poi_snapshot(elements: List[Dict], center_lat: float, center_lng: float, radius_km: float) -> List[Dict]:
    rows: List[Dict] = []
    for e in elements:
        tags = e.get("tags", {})
        category_l2 = _infer_category_l2(tags)
        if not category_l2:
            continue
        point = _get_lat_lng(e)
        if not point:
            continue
        lat, lng = point
        if haversine_km(center_lat, center_lng, lat, lng) > radius_km:
            continue
        name = tags.get("name") or f"{category_l2}_{e.get('type','node')}_{e.get('id','x')}"
        rows.append(
            {
                "name": name,
                "category_l2": category_l2,
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "rating": _infer_rating(tags, category_l2),
                "review_count": _infer_popularity(tags),
                "price_band": PRICE_BAND_BY_L2.get(category_l2, 2),
                "open_status": _infer_open_status(tags),
            }
        )

    dedup = {}
    for r in rows:
        key = (r["name"], r["category_l2"], r["lat"], r["lng"])
        cur = dedup.get(key)
        if cur is None or r["review_count"] > cur["review_count"]:
            dedup[key] = r
    return list(dedup.values())


def _count_tags(elements: List[Dict], key: str, value: Optional[str] = None) -> int:
    total = 0
    for e in elements:
        tags = e.get("tags", {})
        if key not in tags:
            continue
        if value is None or tags.get(key) == value:
            total += 1
    return total


def suggest_radius_km(center_lat: float, center_lng: float, poi_rows: List[Dict], fallback_km: float) -> float:
    if len(poi_rows) < 15:
        return fallback_km
    dists = sorted(haversine_km(center_lat, center_lng, r["lat"], r["lng"]) for r in poi_rows)
    q75_idx = int(0.75 * (len(dists) - 1))
    radius = dists[q75_idx] * 1.15
    return round(clamp(radius, MIN_RADIUS_KM, MAX_RADIUS_KM), 2)


def build_region_context(elements: List[Dict], poi_rows: List[Dict]) -> Dict[str, float]:
    residential = _count_tags(elements, "building", "residential") + _count_tags(elements, "landuse", "residential")
    office = _count_tags(elements, "office") + _count_tags(elements, "building", "commercial")
    transit = (
        _count_tags(elements, "public_transport")
        + _count_tags(elements, "railway", "station")
        + _count_tags(elements, "highway", "bus_stop")
    )
    commercial_poi = sum(1 for r in poi_rows if r["open_status"] == "open")

    office_res_ratio = (office + 1) / (residential + 1)
    day_night = clamp(35 + commercial_poi * 0.7 + office_res_ratio * 12, 20, 95)
    accessibility = clamp(30 + transit * 4.2 + math.log1p(commercial_poi) * 8, 15, 95)
    rent_proxy = clamp(25 + commercial_poi * 0.5 + accessibility * 0.35 + office_res_ratio * 8, 20, 95)
    return {
        "day_night_population_proxy": round(day_night, 2),
        "office_residential_ratio": round(clamp(office_res_ratio, 0.2, 3.0), 2),
        "accessibility_proxy": round(accessibility, 2),
        "rent_proxy": round(rent_proxy, 2),
    }


def write_poi_snapshot(path: str, poi_rows: List[Dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["name", "category_l2", "lat", "lng", "rating", "review_count", "price_band", "open_status"],
        )
        writer.writeheader()
        writer.writerows(poi_rows)


def write_json(path: str, payload: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _fallback_center_from_poi(poi_rows: List[Dict]) -> Tuple[float, float]:
    if not poi_rows:
        return 0.0, 0.0
    return (median([x["lat"] for x in poi_rows]), median([x["lng"] for x in poi_rows]))


def acquire_market_inputs(
    region_query: str,
    output_dir: str,
    default_radius_km: float = DEFAULT_RADIUS_KM,
    countrycodes: Optional[str] = None,
    emit_progress: Optional[Callable[[str, str], None]] = None,
) -> Dict:
    os.makedirs(output_dir, exist_ok=True)
    if emit_progress:
        emit_progress("geocoding", f"解析地理位置: {region_query}")
    center = geocode_region(region_query, countrycodes=countrycodes)

    if emit_progress:
        emit_progress("poi_fetch", "抓取 OSM POI/区域特征")
    elements = fetch_overpass(center.lat, center.lng, default_radius_km)
    poi_rows = build_poi_snapshot(elements, center.lat, center.lng, default_radius_km)

    if len(poi_rows) < 20:
        time.sleep(1.0)
        expanded = clamp(default_radius_km + 0.4, MIN_RADIUS_KM, MAX_RADIUS_KM)
        elements = fetch_overpass(center.lat, center.lng, expanded)
        poi_rows = build_poi_snapshot(elements, center.lat, center.lng, expanded)
        default_radius_km = expanded

    if emit_progress:
        emit_progress("derive_context", "生成 context 与建议半径")
    if not poi_rows:
        raise RuntimeError("未获取到有效 POI，请更换区域关键词或稍后重试。")
    suggested_radius_km = suggest_radius_km(center.lat, center.lng, poi_rows, default_radius_km)
    region_context = build_region_context(elements, poi_rows)

    if emit_progress:
        emit_progress("write_outputs", "写入输入文件")
    poi_path = os.path.join(output_dir, "poi_snapshot.csv")
    context_path = os.path.join(output_dir, "region_context.json")
    write_poi_snapshot(poi_path, poi_rows)
    write_json(context_path, region_context)

    return {
        "region_query": region_query,
        "resolved_location": center.display_name,
        "center_lat": center.lat,
        "center_lng": center.lng,
        "radius_km": suggested_radius_km,
        "poi_count": len(poi_rows),
        "poi_path": poi_path,
        "context_path": context_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="自动获取 market-analysis 输入数据")
    parser.add_argument("--region-query", required=True, help="区域关键词，如: 上海 徐汇区 徐家汇")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--default-radius-km", type=float, default=DEFAULT_RADIUS_KM, help="初始抓取半径 km")
    parser.add_argument("--countrycodes", default=None, help="国家代码过滤，如 cn")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = acquire_market_inputs(
        region_query=args.region_query,
        output_dir=args.output_dir,
        default_radius_km=args.default_radius_km,
        countrycodes=args.countrycodes,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
