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
  --output outputs \
  --center-lat 31.2304 \
  --center-lng 121.4737 \
  --radius-km 1.5
```

## 自动获取输入数据

默认使用高德（`amap`），OSM 作为兜底：

```bash
export AMAP_API_KEY="你的高德Key"

node data_acquisition.js \
  --region-query "安徽省宿州市" \
  --output-dir data/runtime_inputs \
  --data-source amap
```

输出中会返回：

- `center_lat`
- `center_lng`
- `radius_km`
- `poi_path`
- `context_path`

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
