# 部署检查清单

## 环境

- [ ] Raspberry Pi 5 (8GB+) 已联网
- [ ] Node.js 20+ 已安装
- [ ] 外部安卓采集设备可用
- [ ] 账号已登录并可访问目标页面

## 配置

- [ ] 配置任务源输入（店铺清单）
- [ ] 配置并发与限流参数
- [ ] 配置数据存储目录与备份策略

## 验收

- [ ] 20店小样本采集成功
- [ ] 输出 store/product/review 三张事实表
- [ ] 输出 quality_report
- [ ] 验证失败降级到 manual_review_queue
- [ ] `GET /queue_stats`、`GET /quality_report` 可正常返回
- [ ] `scripts/run_daily_incremental.sh` 可被定时任务调用
