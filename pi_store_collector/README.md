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
  --account-id acc-01 \
  --platforms meituan,dianping,douyin \
  --loop false \
  --use-mock-adapter true
```

> 当前开发环境不是树莓派也可执行上述测试；迁移到树莓派后可复用同样脚本。

## 美团自动化采集（ADB）

当前已实现 `meituan-adb-adapter`：可执行“启动美团 -> 点击搜索 -> 输入店名 -> 回车搜索 -> UI dump 解析 -> 回传结果”。

```bash
node src/collector_worker.js \
  --server-url http://127.0.0.1:9080 \
  --device-id <adb_serial> \
  --account-id acc-01 \
  --platforms meituan \
  --loop false \
  --use-meituan-adb true \
  --artifact-root ./data/artifacts
```

可选环境变量：
- `USE_MEITUAN_ADB=true`
- `COLLECTOR_ARTIFACT_ROOT=./data/artifacts`

说明：
- 当前版本基于 UI 文本启发式解析，`confidence` 默认为较低值（0.4）。
- 真实机型上建议按分辨率调整搜索框点击坐标（后续可进一步做控件定位）。

开发机无真机时可运行 dry-run：

```bash
node scripts/test_meituan_adapter_dryrun.js
```
