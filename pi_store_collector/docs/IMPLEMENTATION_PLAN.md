# 实现计划（Pi 5 + 外部安卓采集）

## 目标

输入店铺名称+位置，输出美团/点评/抖音的店铺、商品、评价数据，并生成质量报告。

## 阶段拆分

1. Phase 1 - 核心骨架
- 建立任务队列与任务状态机（PENDING/RUNNING/SUCCESS/FAILED/MANUAL_REVIEW）
- 建立结果落库表：store_facts/product_facts/review_facts/crawl_audit
- 建立结果融合器与质量报告生成器

2. Phase 2 - 采集通信
- 提供任务分发接口：`pull_task`
- 提供结果回传接口：`push_result`
- 提供设备心跳：`heartbeat`
- 支持设备/账号轮转策略

3. Phase 3 - 调度与稳定性
- 每日增量任务调度（cron/systemd timer）
- 限流、重试、降级到人工队列
- 原始证据归档（截图/页面片段）

4. Phase 4 - 验收
- 小样本20店跑通
- 连续7天稳定性验证
- 覆盖率、缺失率、低置信度指标达标

## 交付物

- 可运行服务（Pi端）
- 可运行采集客户端协议（安卓端接入规范）
- CSV/JSON输出与质量报告
