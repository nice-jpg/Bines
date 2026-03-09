import csv
import json
import math
import os
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional

INDUSTRIES = ["餐饮", "零售", "生活服务", "教育培训", "健康服务", "文娱"]

DEFAULT_WEIGHTS = {
    "demand_score": 30.0,
    "competition_score": 25.0,
    "unit_economics_score": 30.0,
    "stability_score": 15.0,
}

INDUSTRY_PROFILES = {
    "餐饮": {
        "time_signal": 82,
        "repurchase": 75,
        "gross_margin_feasibility": 65,
        "seasonality": 40,
        "policy_sensitivity": 35,
        "supply_chain_complexity": 65,
        "space_intensity": 70,
        "payback_months": [10, 18],
        "first_store_model": "轻正餐/快餐单店",
        "budget_level": "中",
    },
    "零售": {
        "time_signal": 70,
        "repurchase": 60,
        "gross_margin_feasibility": 60,
        "seasonality": 55,
        "policy_sensitivity": 30,
        "supply_chain_complexity": 55,
        "space_intensity": 55,
        "payback_months": [12, 24],
        "first_store_model": "高周转小店",
        "budget_level": "中",
    },
    "生活服务": {
        "time_signal": 68,
        "repurchase": 85,
        "gross_margin_feasibility": 70,
        "seasonality": 30,
        "policy_sensitivity": 20,
        "supply_chain_complexity": 35,
        "space_intensity": 45,
        "payback_months": [8, 14],
        "first_store_model": "预约制服务门店",
        "budget_level": "中低",
    },
    "教育培训": {
        "time_signal": 62,
        "repurchase": 65,
        "gross_margin_feasibility": 72,
        "seasonality": 75,
        "policy_sensitivity": 80,
        "supply_chain_complexity": 25,
        "space_intensity": 60,
        "payback_months": [14, 28],
        "first_store_model": "小班课/工作坊",
        "budget_level": "中高",
    },
    "健康服务": {
        "time_signal": 66,
        "repurchase": 70,
        "gross_margin_feasibility": 75,
        "seasonality": 25,
        "policy_sensitivity": 65,
        "supply_chain_complexity": 40,
        "space_intensity": 50,
        "payback_months": [12, 22],
        "first_store_model": "轻医疗/康复咨询",
        "budget_level": "中高",
    },
    "文娱": {
        "time_signal": 74,
        "repurchase": 55,
        "gross_margin_feasibility": 58,
        "seasonality": 60,
        "policy_sensitivity": 40,
        "supply_chain_complexity": 45,
        "space_intensity": 65,
        "payback_months": [12, 24],
        "first_store_model": "主题体验小馆",
        "budget_level": "中",
    },
}


@dataclass
class IndustryMetrics:
    industry: str
    demand_score: float
    competition_score: float
    unit_economics_score: float
    stability_score: float
    total_score: float
    recommendation: str


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalize(values: Dict[str, float], higher_is_better: bool = True) -> Dict[str, float]:
    if not values:
        return {}
    min_v = min(values.values())
    max_v = max(values.values())
    if max_v == min_v:
        return {k: 50.0 for k in values}
    out = {}
    for k, v in values.items():
        scaled = (v - min_v) / (max_v - min_v) * 100.0
        if not higher_is_better:
            scaled = 100.0 - scaled
        out[k] = clamp(scaled)
    return out


def load_dictionary(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["category_to_l1"]


def load_region_context(path: str) -> Dict[str, float]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_poi(path: str) -> List[Dict[str, object]]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(
                {
                    "name": r["name"].strip(),
                    "category_l2": r["category_l2"].strip(),
                    "lat": float(r["lat"]),
                    "lng": float(r["lng"]),
                    "rating": float(r["rating"]),
                    "review_count": int(r["review_count"]),
                    "price_band": int(r["price_band"]),
                    "open_status": r["open_status"].strip(),
                }
            )
    return rows


def deduplicate_poi(poi_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    best = {}
    for row in poi_rows:
        key = (
            row["name"].lower(),
            row["category_l2"].lower(),
            round(float(row["lat"]), 4),
            round(float(row["lng"]), 4),
        )
        cur = best.get(key)
        if cur is None or (row["review_count"], row["rating"]) > (cur["review_count"], cur["rating"]):
            best[key] = row
    return list(best.values())


def filter_region(
    poi_rows: List[Dict[str, object]], center_lat: float, center_lng: float, radius_km: float
) -> List[Dict[str, object]]:
    in_region = []
    for row in poi_rows:
        dist = haversine_km(center_lat, center_lng, float(row["lat"]), float(row["lng"]))
        if dist <= radius_km:
            in_region.append(row)
    return in_region


def assign_industry(poi_rows: List[Dict[str, object]], category_map: Dict[str, str]) -> List[Dict[str, object]]:
    out = []
    for row in poi_rows:
        r = dict(row)
        r["industry"] = category_map.get(row["category_l2"], "生活服务")
        out.append(r)
    return out


def suspicious_outlier(row: Dict[str, object]) -> bool:
    return float(row["rating"]) >= 4.9 and int(row["review_count"]) <= 3


def compute_scores(
    poi_rows: List[Dict[str, object]],
    region_context: Dict[str, float],
    weights: Optional[Dict[str, float]] = None,
) -> List[IndustryMetrics]:
    if weights is None:
        weights = DEFAULT_WEIGHTS
    area = math.pi * 1.0

    by_industry: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in poi_rows:
        by_industry[str(row["industry"])].append(row)

    count_raw = {}
    review_density_raw = {}
    rating_stability_raw = {}
    hhi_raw = {}
    survival_raw = {}
    price_fit_raw = {}

    rent_proxy = float(region_context["rent_proxy"])
    office_residential_ratio = float(region_context["office_residential_ratio"])
    pop_proxy = float(region_context["day_night_population_proxy"])
    accessibility = float(region_context["accessibility_proxy"])

    for industry in INDUSTRIES:
        rows = by_industry.get(industry, [])
        if not rows:
            count_raw[industry] = 0.0
            review_density_raw[industry] = 0.0
            rating_stability_raw[industry] = 0.0
            hhi_raw[industry] = 1.0
            survival_raw[industry] = 0.0
            price_fit_raw[industry] = 0.0
            continue

        open_rows = [r for r in rows if r["open_status"] == "open"]
        count_raw[industry] = len(open_rows) / area
        review_density_raw[industry] = sum(math.log1p(int(r["review_count"])) for r in open_rows) / area
        rating_stability_raw[industry] = (
            sum(float(r["rating"]) * math.log1p(int(r["review_count"])) for r in open_rows)
            / max(1.0, sum(math.log1p(int(r["review_count"])) for r in open_rows))
        )

        chain_counts = defaultdict(int)
        for r in open_rows:
            normalized_name = str(r["name"]).split(" ")[0].lower()
            chain_counts[normalized_name] += 1
        total_open = max(1, len(open_rows))
        hhi_raw[industry] = sum((cnt / total_open) ** 2 for cnt in chain_counts.values())

        mature = [r for r in open_rows if int(r["review_count"]) >= 20 and float(r["rating"]) >= 4.0]
        survival_raw[industry] = len(mature) / total_open

        price_values = [int(r["price_band"]) for r in open_rows]
        median_price = sorted(price_values)[len(price_values) // 2]
        sweet_spot = 2
        price_fit_raw[industry] = 100.0 - min(100.0, abs(median_price - sweet_spot) * 35.0)

    count_score = normalize(count_raw, higher_is_better=True)
    review_score = normalize(review_density_raw, higher_is_better=True)
    rating_score = normalize(rating_stability_raw, higher_is_better=True)
    density_inverse_score = normalize(count_raw, higher_is_better=False)
    hhi_inverse_score = normalize(hhi_raw, higher_is_better=False)
    survival_score = normalize(survival_raw, higher_is_better=True)

    metrics = []
    for industry in INDUSTRIES:
        p = INDUSTRY_PROFILES[industry]
        demand = (
            0.35 * count_score[industry]
            + 0.30 * review_score[industry]
            + 0.20 * rating_score[industry]
            + 0.15 * clamp((p["time_signal"] + 0.3 * accessibility + 0.2 * pop_proxy) / 1.5)
        )
        competition = (
            0.45 * density_inverse_score[industry]
            + 0.30 * hhi_inverse_score[industry]
            + 0.25 * survival_score[industry]
        )
        rent_penalty = (rent_proxy * p["space_intensity"]) / 100.0
        office_res_boost = clamp(50 + (office_residential_ratio - 1.0) * 20)
        unit_econ = (
            0.30 * price_fit_raw[industry]
            + 0.25 * p["repurchase"]
            + 0.25 * p["gross_margin_feasibility"]
            + 0.20 * clamp((office_res_boost + 100.0 - rent_penalty) / 2.0)
        )
        stability = (
            0.40 * (100.0 - p["seasonality"])
            + 0.35 * (100.0 - p["policy_sensitivity"])
            + 0.25 * (100.0 - p["supply_chain_complexity"])
        )

        total = (
            demand * weights["demand_score"] / 100.0
            + competition * weights["competition_score"] / 100.0
            + unit_econ * weights["unit_economics_score"] / 100.0
            + stability * weights["stability_score"] / 100.0
        )
        total = round(total, 2)

        if total >= 75:
            recommendation = "优先进入"
        elif total >= 60:
            recommendation = "小规模验证"
        else:
            recommendation = "暂缓"

        metrics.append(
            IndustryMetrics(
                industry=industry,
                demand_score=round(demand, 2),
                competition_score=round(competition, 2),
                unit_economics_score=round(unit_econ, 2),
                stability_score=round(stability, 2),
                total_score=total,
                recommendation=recommendation,
            )
        )
    return sorted(metrics, key=lambda x: x.total_score, reverse=True)


def coverage_ratio(poi_rows: List[Dict[str, object]]) -> float:
    covered = {r["industry"] for r in poi_rows}
    return len(covered) / len(INDUSTRIES)


def build_opportunity_item(metric: IndustryMetrics) -> Dict[str, object]:
    p = INDUSTRY_PROFILES[metric.industry]
    opp = []
    risk = []
    if metric.demand_score >= 65:
        opp.append("需求强度较高，可快速形成首店客流")
    if metric.unit_economics_score >= 65:
        opp.append("单店经济性较优，现金回收周期可控")
    if metric.competition_score >= 60:
        opp.append("竞争压力相对可承受，存在切入窗口")
    if metric.stability_score < 55:
        risk.append("经营稳定性偏弱，需控制季节性与政策波动")
    if metric.competition_score < 50:
        risk.append("竞争较拥挤，需要差异化定位")
    if metric.unit_economics_score < 55:
        risk.append("单店模型脆弱，需先验证成本结构")
    if not opp:
        opp = ["存在细分定位机会，建议小样本验证"]
    if not risk:
        risk = ["主要风险可控，重点关注执行效率"]

    return {
        "industry": metric.industry,
        "total_score": metric.total_score,
        "recommendation": metric.recommendation,
        "opportunity_points": opp,
        "risk_points": risk,
        "validation_actions": [
            "连续7天分时段客流抽样（工作日+周末）",
            "电话访谈10家同类门店，确认租金与人效区间",
            "进行2周最小化产品测试，验证复购与毛利",
        ],
        "estimated_payback_months": f"{p['payback_months'][0]}-{p['payback_months'][1]}个月",
        "first_store_model": p["first_store_model"],
        "budget_level": p["budget_level"],
        "stop_loss_condition": "连续2个月毛利率低于目标线且复购不达预期则止损",
    }


def write_scorecard(path: str, metrics: List[IndustryMetrics]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "industry",
                "demand_score",
                "competition_score",
                "unit_economics_score",
                "stability_score",
                "total_score",
                "recommendation",
            ],
        )
        writer.writeheader()
        for m in metrics:
            writer.writerow(asdict(m))


def write_opportunities(path: str, metrics: List[IndustryMetrics], top_n: int = 3) -> None:
    top = [build_opportunity_item(m) for m in metrics[:top_n]]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(top, f, ensure_ascii=False, indent=2)


def run_weight_sensitivity(
    poi_rows: List[Dict[str, object]], region_context: Dict[str, float]
) -> Dict[str, object]:
    baseline = compute_scores(poi_rows, region_context, DEFAULT_WEIGHTS)
    base_top3 = [m.industry for m in baseline[:3]]

    stability = {}
    for k in DEFAULT_WEIGHTS:
        for factor in (0.8, 1.2):
            modified = deepcopy(DEFAULT_WEIGHTS)
            modified[k] = DEFAULT_WEIGHTS[k] * factor
            total = sum(modified.values())
            for name in modified:
                modified[name] = modified[name] / total * 100.0
            current_top3 = [m.industry for m in compute_scores(poi_rows, region_context, modified)[:3]]
            stability[f"{k}_{factor:.1f}"] = len(set(base_top3) & set(current_top3))
    return {"baseline_top3": base_top3, "overlap_with_variants": stability}


def run_outlier_robustness(
    poi_rows: List[Dict[str, object]], region_context: Dict[str, float]
) -> Dict[str, object]:
    baseline = compute_scores(poi_rows, region_context, DEFAULT_WEIGHTS)
    clean_rows = [r for r in poi_rows if not suspicious_outlier(r)]
    cleaned = compute_scores(clean_rows, region_context, DEFAULT_WEIGHTS)
    return {
        "baseline_top3": [m.industry for m in baseline[:3]],
        "cleaned_top3": [m.industry for m in cleaned[:3]],
        "removed_outliers": len(poi_rows) - len(clean_rows),
        "overlap": len(set(m.industry for m in baseline[:3]) & set(m.industry for m in cleaned[:3])),
    }


def write_report(
    path: str,
    metrics: List[IndustryMetrics],
    coverage: float,
    sensitivity: Dict[str, object],
    robustness: Dict[str, object],
) -> None:
    top = metrics[:3]
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 商圈创业赛道分析报告（MVP）\n\n")
        f.write("## 结论摘要\n\n")
        f.write("- 目标：确定商圈内可优先验证的创业赛道。\n")
        f.write("- 风险偏好：稳健现金流。\n")
        f.write("- 推荐优先级（Top 3）：\n")
        for idx, m in enumerate(top, start=1):
            f.write(f"  - {idx}. {m.industry}（{m.total_score}分，{m.recommendation}）\n")
        f.write("\n## 评分结果\n\n")
        for m in metrics:
            f.write(
                f"- {m.industry}: 总分{m.total_score} | 需求{m.demand_score} | 竞争{m.competition_score}"
                f" | 单店经济性{m.unit_economics_score} | 稳定性{m.stability_score} | 结论{m.recommendation}\n"
            )
        f.write("\n## 测试与稳健性\n\n")
        f.write(f"- 行业覆盖率：{coverage * 100:.1f}%（目标>=80%）\n")
        f.write(f"- 权重敏感性（Top3重合度）：{sensitivity['overlap_with_variants']}\n")
        f.write(
            f"- 异常值鲁棒性：剔除{robustness['removed_outliers']}条可疑样本后，Top3重合{robustness['overlap']}/3\n"
        )
        f.write("\n## 90天验证建议\n\n")
        f.write("- 前2周：完成客流与转化抽样，验证真实需求。\n")
        f.write("- 第3-6周：跑最小化门店模型，确认毛利与复购。\n")
        f.write("- 第7-12周：扩展到第二个点位，验证可复制性。\n")


def run_analysis(
    poi_path: str,
    context_path: str,
    dictionary_path: str,
    center_lat: float,
    center_lng: float,
    radius_km: float,
    output_dir: Optional[str] = None,
    emit_progress: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, object]:
    if emit_progress:
        emit_progress("data_loading", "开始加载输入数据")
    category_map = load_dictionary(dictionary_path)
    region_context = load_region_context(context_path)
    poi_rows = load_poi(poi_path)

    if emit_progress:
        emit_progress("data_cleaning", "执行去重、地理过滤与行业归类")
    poi_rows = deduplicate_poi(poi_rows)
    poi_rows = filter_region(poi_rows, center_lat, center_lng, radius_km)
    poi_rows = assign_industry(poi_rows, category_map)

    if emit_progress:
        emit_progress("scoring", "计算行业评分与结论")
    metrics = compute_scores(poi_rows, region_context, DEFAULT_WEIGHTS)
    coverage = coverage_ratio(poi_rows)

    if emit_progress:
        emit_progress("validation", "执行敏感性与鲁棒性检查")
    sensitivity = run_weight_sensitivity(poi_rows, region_context)
    robustness = run_outlier_robustness(poi_rows, region_context)

    outputs = {}
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        scorecard_path = os.path.join(output_dir, "industry_scorecard.csv")
        opportunities_path = os.path.join(output_dir, "top_opportunities.json")
        report_path = os.path.join(output_dir, "analysis_report.md")
        write_scorecard(scorecard_path, metrics)
        write_opportunities(opportunities_path, metrics, top_n=3)
        write_report(report_path, metrics, coverage, sensitivity, robustness)
        outputs = {
            "industry_scorecard": scorecard_path,
            "top_opportunities": opportunities_path,
            "analysis_report": report_path,
        }

    result = {
        "coverage": coverage,
        "metrics": [asdict(m) for m in metrics],
        "top_opportunities": [build_opportunity_item(m) for m in metrics[:3]],
        "sensitivity": sensitivity,
        "robustness": robustness,
        "outputs": outputs,
    }
    if emit_progress:
        emit_progress("done", "分析完成")
    return result
