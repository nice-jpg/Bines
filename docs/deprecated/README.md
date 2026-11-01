# 历史架构文档

这些文档用于追溯设计与当时实现，不作为新接口规范。

## Runtime 三层重划分前

归档日期：2026-10-31。目标设计见 [三层架构](../agent-system-design.md) 和 [线程模型](../threading-model.md)。
代码迁移已实施；历史快照仅用于追溯，不代表当前接口。

| 文档 | 保留原因 |
| --- | --- |
| [三层实施前方案](runtime-before-hypervisor/three-layer-proposal.md) | 保留设计基线，当前实现使用锁保护 Router 短操作 |
| [三层实施前线程方案](runtime-before-hypervisor/three-layer-threading-proposal.md) | 保留原主循环独占路由提案 |
| [线程与通信实现](runtime-before-hypervisor/threading-model.md) | 2026-10-29 AgentApplication/AgentHost/AgentRouter 实现基线 |
| [更早线程快照](runtime-before-hypervisor/threading-model-current.md) | 2026-10-28 改造前线程与锁分析 |
| [旧线程提案](runtime-before-hypervisor/threading-model-proposal.md) | 已替代的 GatewayManager 方案 |
| [旧 Runtime](runtime-before-hypervisor/agent-runtime-architecture.md) | 单会话运行模型 |
| [旧 Channel](runtime-before-hypervisor/channel-architecture.md) | 旧 GatewayManager/ChannelGateway |
| [旧 Conversation](runtime-before-hypervisor/conversation-architecture.md) | 单会话输入与恢复假设 |

历史正文保留原术语和结论。更新设计时修改现行文档，不向这些文件追加新架构实现。
