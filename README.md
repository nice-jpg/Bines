# 商圈级创业赛道分析 MVP

该项目实现了一个 1 周可落地的商圈创业赛道分析流程（公开数据 + 地图 POI）。

## 功能

- 读取输入表 `poi_snapshot` 与 `region_context`
- 按商圈中心点 + 半径过滤样本
- 行业归类到一级类目：
  - 餐饮 / 零售 / 生活服务 / 教育培训 / 健康服务 / 文娱
- 计算行业评分：
  - 需求强度（30）
  - 竞争压力（25）
  - 单店经济性（30）
  - 经营稳定性（15）
- 输出：
  - `industry_scorecard.csv`
  - `top_opportunities.json`
  - `analysis_report.md`
- 执行测试：
  - 数据完整性
  - 权重敏感性（±20%）
  - 异常值鲁棒性（极端评分样本剔除）

## 目录

```text
.
├── analysis_core.py
├── data_acquisition.py
├── market_analysis.py
├── mcp_server.py
├── openclaw.mcp.json
├── data
│   ├── poi_snapshot.csv
│   ├── region_context.json
│   └── industry_dictionary.json
├── outputs
└── tests
    └── test_market_analysis.py
```

## 运行

```bash
python3 market_analysis.py \
  --poi data/poi_snapshot.csv \
  --context data/region_context.json \
  --dictionary data/industry_dictionary.json \
  --output outputs \
  --center-lat 31.2304 \
  --center-lng 121.4737 \
  --radius-km 1.5
```

## MCP 模式（OpenClaw 接入）

启动 MCP 服务（stdio）：

```bash
python3 mcp_server.py
```

服务提供：

- Tools
  - `acquire_market_inputs`：输入区域关键词，自动生成 `poi/context/center/radius`
  - `auto_acquire_and_analyze`：一键自动取数并完成分析
  - `run_full_analysis`：执行全链路分析并实时发送 `notifications/progress`
  - `get_latest_summary`：读取最近一次摘要
  - `health_check`：服务健康检查
- Resources
  - `analysis://latest/inputs`
  - `analysis://latest/summary`
  - `analysis://latest/scorecard`
  - `analysis://latest/opportunities`

OpenClaw 配置样例见：

- `openclaw.mcp.json`

## 自动获取输入数据（减少人工）

只提供一个区域关键词，自动输出：

- `poi_snapshot.csv`
- `region_context.json`
- `center_lat/center_lng`
- `radius_km`（建议值）

命令：

```bash
python3 data_acquisition.py \
  --region-query "上海 徐家汇" \
  --output-dir data/runtime_inputs \
  --countrycodes cn
```

返回 JSON 中包含可直接传给 `run_full_analysis` 的参数路径与坐标。

## 输入接口

### `poi_snapshot` (CSV)

字段：

- `name`
- `category_l2`
- `lat`
- `lng`
- `rating`
- `review_count`
- `price_band`
- `open_status`

### `region_context` (JSON)

字段：

- `day_night_population_proxy`（0-100）
- `office_residential_ratio`（0-3）
- `accessibility_proxy`（0-100）
- `rent_proxy`（0-100）

## 输出接口

### `industry_scorecard.csv`

字段：

- `industry`
- `demand_score`
- `competition_score`
- `unit_economics_score`
- `stability_score`
- `total_score`
- `recommendation`

### `top_opportunities.json`

字段：

- `industry`
- `total_score`
- `recommendation`
- `opportunity_points`
- `risk_points`
- `validation_actions`
- `estimated_payback_months`
- `first_store_model`
- `budget_level`
- `stop_loss_condition`

## 测试

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```
