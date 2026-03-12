# API 合同（Pi <-> Collector）

## 0) health / queue / report

- `GET /health`：服务与DB健康检查
- `GET /queue_stats`：任务状态与心跳摘要
- `GET /manual_review_queue?status=PENDING&limit=50`：人工复核队列
- `GET /quality_report?lookback_hours=24`：质量报告（成功率、覆盖率、低信号门店）

## 1) pull_task

- Method: POST
- Request:
```json
{
  "device_id": "android-01",
  "platforms": ["meituan", "dianping", "douyin"]
}
```
- Response:
```json
{
  "task_id": "t_20260310_0001",
  "platform": "meituan",
  "store_name": "示例店铺",
  "lat": 33.64,
  "lng": 116.96,
  "review_limit": 30,
  "product_limit": 20
}
```

## 2) push_result

- Method: POST
- Request 包含：`task_id`、`status`、`store_facts`、`product_facts[]`、`review_facts[]`、`artifacts[]`

## 3) heartbeat

- Method: POST
- Request: `device_id`、`account_id`、`platform`、`status`
