# Pi Store Collector

树莓派部署的多平台店铺采集与融合模块（Pi调度+处理，外部安卓设备采集）。

## 目录

- `docs/IMPLEMENTATION_PLAN.md`：实现计划与里程碑
- `docs/API_CONTRACT.md`：Pi与采集端通信协议
- `docs/DEPLOY_CHECKLIST.md`：部署与验收清单
- `config/example.env`：环境变量模板
- `src/server.js`：Pi 服务端（任务队列 API）
- `src/report_service.js`：队列统计与质量报告
- `src/collector_client.js`：采集端 SDK（pull/push/heartbeat）
- `src/collector_worker.js`：采集端 worker 骨架（轮询任务）
- `src/automation/adb_session.js`：统一封装 ADB 驱动流程与留证
- `src/apps/meituan/env.js`：美团环境变量与页面参数
- `src/apps/meituan/logic.js`：美团操作逻辑与结果解析
- `src/adapters/`：平台适配器（当前含 stub/mock）
- `scripts/bootstrap.sh`：初始化脚本
- `scripts/test_local_capabilities.sh`：本地能力测试（语法+单测）
- `scripts/test_api_flow.sh`：本地 API 流程验证（enqueue/pull/push）
- `scripts/enqueue_from_csv.js`：离线/定时入队脚本
- `scripts/export_facts.js`：导出 store/product/review 与质量报告
- `scripts/run_daily_incremental.sh`：每日增量一键脚本（便于 cron/systemd timer）

## 当前实现（Phase 1 + Phase 2 骨架）

### Pi 服务 API

- `GET /health`
- `GET /queue_stats`
- `GET /manual_review_queue`
- `GET /quality_report`
- `POST /enqueue_from_csv`
- `POST /pull_task`
- `POST /push_result`
- `POST /heartbeat`

### 数据表

- `tasks`
- `store_facts`
- `product_facts`
- `review_facts`
- `crawl_audit`
- `heartbeats`
- `manual_review_queue`

### 采集端

- `collector_client`：封装任务拉取、结果回传、心跳
- `collector_worker`：可单次运行或循环轮询
- 默认适配器为 stub（未实现时会返回失败并触发重试/人工队列）
- 可用 mock 适配器联调：`--use-mock-adapter true`

## 快速开始

```bash
cd /Users/nice/Project/misc/Bines/pi_store_collector
cp config/example.env .env
npm run start
```

## 一键本地采集

常用场景不需要单独启动 server，也不需要显式传 `account-id` 或 `use-meituan-adb`。

```bash
cd /Users/nice/Project/misc/Bines/pi_store_collector
npm run collect:local -- --device-id <adb_serial> --poi-csv-path /path/to/poi_snapshot.csv
```

这个命令会自动：
- 启动本地 server
- 把 `poi_snapshot.csv` 入队
- 执行一次 worker 采集
- worker 结束后关闭本地 server

常用可选参数：
- `--platforms meituan`
- `--artifact-root ./data/artifacts`
- `--db-path ./data/pi_store_collector.db`
- `--port 9080`

## 本地测试（开发机）

```bash
# 能力测试（语法+单测）
./scripts/test_local_capabilities.sh

# API 流程测试（服务端全链路）
./scripts/test_api_flow.sh
```

## 导出与日报

```bash
# 1) 入队（可给 cron/systemd timer 调用）
node scripts/enqueue_from_csv.js \
  --poi-csv-path /path/to/poi_snapshot.csv \
  --platforms meituan,dianping,douyin

# 2) 导出事实表与质量报告
node scripts/export_facts.js --out-dir ./data/exports/latest --lookback-hours 24

# 3) 一键日常流程（入队 + 导出）
./scripts/run_daily_incremental.sh /path/to/poi_snapshot.csv
```

## 采集端联调（mock）

```bash
node src/collector_worker.js \
  --server-url http://127.0.0.1:9080 \
  --device-id android-01 \
  --platforms meituan,dianping,douyin \
  --loop false \
  --use-mock-adapter true
```

> 当前开发环境不是树莓派也可执行上述测试；迁移到树莓派后可复用同样脚本。

## 美团自动化采集（ADB）

当前已实现 `meituan-adb-adapter`：可执行“启动美团 -> 点击搜索 -> 输入店名 -> 回车搜索 -> UI dump 解析 -> 回传结果”。

当前架构已调整为：
- 所有基于 ADB 的公共流程统一收敛到 `src/automation/adb_session.js`
- 每个应用按模块拆分，至少包含：
- `env.js`：应用环境变量、等待时间、页面入口参数
- `logic.js`：应用操作逻辑、视图选择、结果解析

```bash
node src/collector_worker.js \
  --server-url http://127.0.0.1:9080 \
  --device-id <adb_serial> \
  --platforms meituan \
  --loop false \
  --artifact-root ./data/artifacts
```

可选环境变量：
- `COLLECTOR_ARTIFACT_ROOT=./data/artifacts`

说明：
- 当前版本基于 UI 文本启发式解析，`confidence` 默认为较低值（0.4）。
- `account-id` 默认直接使用 `device-id`，通常不需要单独传。
- `meituan` 默认直接使用 ADB 适配器，通常不需要再传开关。
- 真实机型上建议按分辨率调整搜索框点击坐标（后续可进一步做控件定位）。

开发机无真机时可运行 dry-run：

```bash
node scripts/test_meituan_adapter_dryrun.js
```

设备真机探测：

```bash
node scripts/probe_android_capabilities.js --device-id <adb_serial>
```

如果 `uiautomator dump` 失败，当前适配器会在失败信息里附加截图路径，便于确认美团页面是否已打开、搜索框是否获得焦点。
