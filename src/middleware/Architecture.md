# Middleware Architecture

当前完整目录、引用关系和七阶段流程见 [Middleware 分类](../../docs/middleware-catalog.md)。页面业务设计见 [页面策略架构](../../docs/page-strategy-design.md)。本文保留装配维护约束，避免重复维护多套时序图。

## 装配

AgentEngine._build_graph 保留通用能力：HostTrace、ModelRetry、ConversationInput、业务 middleware、ToolError、ToolTrace、LLMTrace。

ConductorEngine 的业务 middleware 依次为 ProbeLifecycleMiddleware、StrategyDispatchMiddleware、ToolExecutionPolicyMiddleware。CollectorEngine 的业务能力由自身装配，不加入 conductor 的策略分发。

## 组合关系

- StrategyDispatchMiddleware 由 AgentEngine 自动装配，通过 Engine 的选择接口和冻结注册表调用策略 hook，不限定 Conductor 或页面场景。
- GeneralStrategy 组合 PageVisitController、PageInputMiddleware、DeviceOperationReliabilityMiddleware 和 CaptchaHumanInTheLoopMiddleware。
- MerchantStrategy 组合通用策略与 CollectionPersistenceMiddleware，只对明确声明的 merchant 访问实施训练和完成门禁。
- ToolExecutionPolicyMiddleware 组合 DangerousToolMiddleware 与 OperationNoticeMiddleware。
- PageInputMiddleware 组合 PageManualMiddleware 与 DeviceContextCompressionMiddleware。

ProbeControlMiddleware、PageContextMiddleware、CollectorReadinessMiddleware 已删除。工具和页面状态的唯一归属、同步/异步方法保持一致，不通过旧路径转发维持双套组合。

## 维护约束

更改装配顺序、内部委托、状态协议、工具结果或 interrupt 行为时，同步更新上述分类文档与页面策略文档。工具返回时序与 before/after_model 顺序必须分别核对。页面策略不得修改 AgentEngine 的基础通信、重试和追踪职责。

通用 Strategy、注册表和分发器位于 `src/agent_runtime/strategy`；页面上下文、可靠性与验证码位于 `src/strategy/general`；商家训练门禁和采集适配位于 `src/strategy/merchant`。本目录保留全局探测、危险操作/通知及 tool_io。

ConductorState 组合 general、merchant、probe 三个子模块；场景字段分别在所属策略的 state.py 中声明，全局 ProbeState 和根 schema 位于 conductor/state.py。局部更新完成合并后再提交完整子模块，不能依赖 graph 递归合并。Collector 的训练职责独立，不复用商家遍历阶段。
