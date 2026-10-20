# Agent Runtime

运行层采用 `AgentWorker → AgentCoordinator → AgentEngine`。业务工具、提示词和 middleware
集中在 `ConductorEngine`、`CollectorEngine` 等派生类中；会话实现独立于 LangChain。
详见 [Runtime 架构](../../docs/agent-runtime-architecture.md)、
[会话管理](../../docs/conversation-architecture.md) 和 [通信层](../../docs/channel-architecture.md)。

## 公共接口与所有权

- `AgentWorker(coordinator)`：`start()` 启动 Agent 线程，`join()` 返回结果或传播异常。
- `AgentCoordinator(engine)`：根据 `EngineResult` 选择继续、恢复或返回，使用 Engine 的会话发送结果。
- `AgentEngine(..., gateway_id=...)`：要求明确 ID，自行创建并持有 `ConversationSession`。四个执行方法仍为 `run_turn()`、`continue_turn()`、`resume_turn()`、`close()`。
- `EngineResult`：包含执行状态、输出和中断种类，不携带回复地址或 graph state。

Engine 不维护多会话字典、ready 队列、父子通信、线程归属标记或执行锁。
执行状态集中于 `ExecutionState`，生命周期使用 `EngineLifecycle`。
`session_id` 构造参数仅指定 checkpoint ID，与渠道 conversation_id 无关；默认每个 Engine 独立生成。

## 子类扩展与统一装配

`AgentEngine._build_graph` 统一装配工具、基础及业务 middleware、checkpointer 和 trace。
`ConversationInputMiddleware` 与其余 middleware 一起注册到 graph，Engine 不保存独立成员。
Engine 与 middleware 共用无状态的 `middleware.communication.read_pending_input` 转换及确认消息块。

子类覆盖 `_create_initial_messages() -> Sequence[InputMessage]` 自定义启动输入。
该接口首次 `run_turn()` 时才调用，构造函数不接受 `initial_messages`，也不保存 `_initial`。
默认返回空序列，表示等待渠道输入；Collector 根据 assignment、Subagent 根据任务消息生成首轮输入。
执行开始后，后续新轮次、继续及恢复不重复生成启动任务。

## 输入与通知

`ConversationSession` 首次接收时固定 channel_id/conversation_id。一个批次的积压发言
保留发送者、正文和顺序，形成一个不可变 `MessageBlock`；不逐条构造 user input。
`ConversationInputMiddleware` 在安全模型边界转换为一条带稳定 ID 的 HumanMessage，
根据 graph 中实际存在的 ID 确认输入。重试保留原块；新消息不会重写已准备的块。
模型和工具执行期间的观测只收集消息，不提前封装下一份输入。

通知通过 `GatewayManager.instance().send_message(gateway_id, message)` 发送。
框架适配器为每次工具调用绑定不可变地址值，支持首次入站发生在初始任务执行中的情况；
上下文在调用结束后恢复，不向工具传递 Session/Engine 引用，也不继承父 Engine 的默认回复地址。
尚未绑定会话时发送明确失败。工具 `talk` 仅暴露 message 参数，不包含 reply_to。

多个 Engine 可显式绑定同一 Gateway，但各自拥有 Session 和 graph/history；外部必须保证互斥执行。
消息不会自动广播或跨 Engine 复制；有本地积压时不应切换消费者，会话交接策略不在本模块实现。

## 恢复与关闭

CONTINUE_REQUIRED 触发 continue_turn；RESUME_READY 触发 resume_turn。
liveness、模型传输和递归中断自动恢复；人工中断由会话模块等待精确 `done`，永久执行错误等待“继续”。
确认消息仍保留为真实发言，满足恢复条件不等于消息已经进入模型上下文。

Conductor 完成协议保持为：

```json
{"resolved": true, "summary": "Task completed", "result": {}, "next_action": null}
```

Engine.close 幂等关闭自己的 Session，记录未确认消息数量，不关闭 Gateway。
入口通过 Manager 停止输入，等待 Agent/工具生产者结束，再结束出站、等待投递排空和释放 Gateway。
`raw_run` 使用 ConsoleChannel，训练使用 TrainingChannel；二者都显式注册 Gateway。

所有 graph.invoke 使用 `durability="exit"`。InMemorySaver 提供进程内 checkpoint，
不承诺进程崩溃恢复或旧逐条消息历史的自动迁移。基础 tracing、模型重试和工具错误处理保持原有职责。

## 目录职责

- `worker.py`、`coordinator.py`、`engine.py`：线程管理、执行调度及 graph 装配与调用。
- `types/interrupts.py`：框架中断载荷的解析与分类。
- `middleware/communication.py`：合并消息块输入适配、调用级地址绑定、主动通知及 talk 工具；会话状态仍属于 conversation 模块。
- `middleware/model_retry.py`、`middleware/tool_error.py`：模型重试及工具错误处理。
- `types/execution.py`：生命周期、执行状态、结果和让出原因。
- `types/framework.py`、`types/response.py`：框架泛型边界及结构化完成响应。

- `messaging/messages.py`：原始收发消息和输入流关闭异常，不依赖执行框架。
- `conversation/`：固定会话、积压消息与输入块管理，由 Engine 创建并持有。

根包不聚合导出执行类。调用方使用 `agent_runtime.engine`、`agent_runtime.coordinator`、
`agent_runtime.worker`、`agent_runtime.types.execution` 等定义模块；Channel/Gateway 仅导入
`agent_runtime.messaging.messages`，不会触发 Engine 或 LangChain 加载。
历史调查见 [Runtime 内存调查](../../docs/runtime-memory-investigation.md)，其中旧路径仅作历史证据。
