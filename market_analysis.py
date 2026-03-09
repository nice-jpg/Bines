#!/usr/bin/env python3
import argparse

from analysis_core import (
    DEFAULT_WEIGHTS,
    INDUSTRIES,
    IndustryMetrics,
    assign_industry,
    build_opportunity_item,
    clamp,
    compute_scores,
    coverage_ratio,
    deduplicate_poi,
    filter_region,
    haversine_km,
    load_dictionary,
    load_poi,
    load_region_context,
    normalize,
    run_analysis,
    run_outlier_robustness,
    run_weight_sensitivity,
    suspicious_outlier,
    write_opportunities,
    write_report,
    write_scorecard,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="商圈创业赛道分析")
    parser.add_argument("--poi", required=True, help="poi_snapshot CSV 路径")
    parser.add_argument("--context", required=True, help="region_context JSON 路径")
    parser.add_argument("--dictionary", required=True, help="行业映射字典 JSON 路径")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--center-lat", type=float, required=True, help="商圈中心纬度")
    parser.add_argument("--center-lng", type=float, required=True, help="商圈中心经度")
    parser.add_argument("--radius-km", type=float, default=1.5, help="分析半径（km）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_analysis(
        poi_path=args.poi,
        context_path=args.context,
        dictionary_path=args.dictionary,
        center_lat=args.center_lat,
        center_lng=args.center_lng,
        radius_km=args.radius_km,
        output_dir=args.output,
    )
    print("Analysis completed.")
    print(f"Coverage ratio: {result['coverage'] * 100:.1f}%")
    print("Outputs:")
    print(f"- {result['outputs']['industry_scorecard']}")
    print(f"- {result['outputs']['top_opportunities']}")
    print(f"- {result['outputs']['analysis_report']}")


if __name__ == "__main__":
    main()
