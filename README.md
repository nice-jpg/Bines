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
