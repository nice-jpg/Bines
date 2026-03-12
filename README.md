# 商圈级创业赛道分析 MVP（JavaScript）

该项目已重构为 JavaScript（Node.js）实现，并保持 MCP 协议与工具接口不变。

## 核心能力

- 输入 `poi_snapshot` 与 `region_context`
- 按商圈中心点 + 半径过滤样本
- 行业归类（餐饮/零售/生活服务/教育培训/健康服务/文娱）
- 评分输出（需求30/竞争25/单店经济性30/稳定性15）
- MCP 实时汇报（`notifications/progress`）

## 目录

```text
.
├── analysis_core.js
├── data_acquisition.js
├── market_analysis.js
├── mcp_server.js
├── openclaw.mcp.json
├── data
│   ├── poi_snapshot.csv
│   ├── region_context.json
│   └── industry_dictionary.json
└── tests
```

## CLI 运行

```bash
node market_analysis.js \
  --poi data/poi_snapshot.csv \
  --context data/region_context.json \
  --dictionary data/industry_dictionary.json \
  --output outputs
```

说明：若 `--poi` 同目录存在 `analysis_scope.json`（由采集阶段生成），分析会自动使用其中的 `center_lat/center_lng/radius_km`，无需重复传坐标。

## 自动获取输入数据

默认使用高德（`amap`），OSM 作为兜底：

```bash
export AMAP_API_KEY="你的高德Key"

node data_acquisition.js \
  --region-query "安徽省宿州市" \
  --output-dir data/runtime_inputs \
  --data-source amap \
  --amap-max-pages 8
```

输出中会返回：

- `center_lat`
- `center_lng`
- `radius_km`
- `poi_path`
- `context_path`

说明：`--amap-max-pages` 默认 `8`，每页最多 `25` 条，理论上限约 `200` 条（受高德实际返回与限流影响）。

## 调试脚本（scripts）

调试用途的 raw POI 抓取已独立到 `scripts/`，不影响主流程逻辑：

```bash
export AMAP_API_KEY=\"你的高德Key\"

node scripts/fetch_amap_raw_pois.js \
  --region-query \"安徽省宿州市\" \
  --output-file data/debug/amap_pois_raw_300.json \
  --radius-km 1.5 \
  --page-size 25 \
  --max-pages 12 \
  --total-limit 300
```

## MCP 模式（OpenClaw）

启动：

```bash
node mcp_server.js
```

Tools（保持不变）：

- `run_full_analysis`
- `acquire_market_inputs`
- `auto_acquire_and_analyze`
- `get_latest_summary`
- `health_check`

Resources：

- `analysis://latest/inputs`
- `analysis://latest/summary`
- `analysis://latest/scorecard`
- `analysis://latest/opportunities`

OpenClaw 配置样例见 [openclaw.mcp.json](/Users/nice/Project/misc/Bines/openclaw.mcp.json)。

## 测试

```bash
node --test tests/*.test.js
```

## 门店事实数据补全

使用 `scripts/enrich_store_facts.js` 逐店补充多源指标并输出店铺事实表：

```bash
node scripts/enrich_store_facts.js \
  --poi data/runtime_inputs_sync/poi_snapshot.csv \
  --output data/facts/store_facts.csv \
  --env-file .env \
  --city 宿州
```

可选：

- `--meituan-csv`：美团导出表（推荐含 `name/rating/review_count/review_count_7d/review_count_30d/review_activity_days_30d/rating_stability_30d/avg_price`）
- `--dianping-csv`：点评导出表（同上字段口径）
- `--delay-ms`：每店请求间隔，默认 `280`

口径说明（重要）：

- 本模块不输出真实订单真值：`fused_order_count`固定为空。
- 统一写入 `order_missing_reason=NO_AUTHORIZED_ORDER_SOURCE`。
- 以 `demand_proxy_score` 替代订单强度，计算公式：
  - `0.45*评论规模分 + 0.30*评论增速分 + 0.15*活跃连续性分 + 0.10*评分稳定性分`
- 输出同时包含 `demand_proxy_level(A/B/C)` 与 `low_signal_flag`。
- 额外生成质量报告：`*_quality_report.json`，含 `proxy_coverage_rate / order_truth_coverage_rate / low_signal_store_count`。
- 该代理分仅用于横向比较与趋势观察，不代表平台真实订单数。

## 从0开始的店铺事实采集流水线

脚本：`scripts/store_facts_pipeline.js`

输入：

- 必填：`--poi`（店铺清单 CSV）
- 可选：
  - `--meituan-csv` / `--dianping-csv`（授权导出）
  - `--meituan-url-csv` / `--dianping-url-csv`（公开页面 URL 清单，在线提取可见指标）

输出：

- `store_facts.csv`（每店一行）
- `store_facts_quality_report.json`

示例：

```bash
node scripts/store_facts_pipeline.js \
  --poi data/runtime_inputs_sync/poi_snapshot.csv \
  --output data/facts/store_facts_v2.csv \
  --meituan-csv data/sources/meituan_export.csv \
  --dianping-csv data/sources/dianping_export.csv \
  --match-threshold 0.75
```
