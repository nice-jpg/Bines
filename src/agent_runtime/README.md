# Agent Runtime

Runtime 只负责独立 Agent 的运行，不管理 Channel、Root 或业务类型。
完整装配见 [三层架构](../../docs/agent-system-design.md)，线程边界见 [线程模型](../../docs/threading-model.md)。

## 模块

| 模块 | 职责 |
| --- | --- |
| engine.py | 独立 graph、checkpoint、执行配置、trace 和结果验收 |
| coordinator.py | 续跑、恢复与中断，不把自然语言结尾隐式发送到 Channel |
| worker.py | 每个 Agent 一个线程及事件循环，任务结束后继续监听 |
| contracts.py | AgentHandle、MailboxToken、通信及派生端口的类型契约 |
| mailbox.py | 持有 token 的本地通信门面，不暴露 Router 或队列 |
| tasks.py | 每个 Agent 的 received/sent 任务管理器，区分最终报告与父方验收 |
| conversation/ | 暂存 mail、稳定消息块、历史和 checkpoint 消费确认 |
| fork.py | 记录安全模型边界，派生时复制语义 state |
| messaging/tools.py | 固定的 spawn_subagent、list_subagents、call、report、accept_report、kill 工具 |
| middleware/ | 消息注入、模型重试、工具异常与追踪 |
| strategy/ | 按 state.active_strategy 路由业务策略 |

## 装配

```python
host = Host()
root = AssistantEngine(model=model, handle=host.register_root())
await host.run(root, [channel])
```

Host、RootAgent 位于 agent_hypervisor；AssistantEngine 位于 application。
Host 统一负责通信服务和 Worker 生命周期，不再提供独立 AgentApplication。
多个 Channel 共用一个 Root，session 只是外部对话地址，不绑定 Agent 身份。
业务使用 handle.spawner 申请子身份并显式构造专用 Engine，不传 factory 回调。

## 独立 Engine 运行契约

每个 Engine 拥有自己的 graph 和 saver。同步与异步入口均使用显式 RunnableConfig，
异步执行在该 config 的独立 Task context 中完成；不继承父 graph task/checkpoint namespace。
invoke/ainvoke 使用 durability="exit"。Worker 使用异步 Coordinator 路径，空闲时 await Mailbox.receive。
父子不共享消息历史，父任务不会因为子任务未结束而自动被禁用工具。

模型本轮返回后，Engine 先处理已到达的消息；若仍有委派任务且没有最终报告等待验收，
返回 WAITING 而非 CONTINUE_REQUIRED。Coordinator 结束本轮，Worker 下一轮阻塞监听同一邮箱，
不调用模型轮询。新任务、进展或最终报告均可唤醒；最终报告进入验收流程，不自动清除任务。
同步入口使用阻塞读取，异步 Worker 使用 await receive。监听不表示任务完成，也不限制本轮工具。

list_subagents 只查询当前 Agent 的存活直接子 Agent，返回类型及任务主题/状态，不暴露通信地址。
验收任务后子 Agent 仍保留；父 Agent 根据后续用途复用或 kill。页面绑定 collector 仅可复用在同一访问中。

固定 middleware 顺序：HostTrace → ModelRetry → ConversationInput → ForkContext → ToolError →
StrategyDispatch → ToolTrace → LLMTrace。没有业务策略的 Engine 不注册 StrategyDispatch。
ForkContext 在工具执行前保存已完整配对的消息边界；实际派生才深复制，重新绑定子 Engine 工具与邮箱。

## 通信

call(subagent_id, task_id, task) 入箱后立即返回。task_id 是父 Agent 提供的简短任务主题，
技术关联 ID 只保存在内部；模型消息不包含发送者、Channel、批次或 sequence。
外部输入 task_id 为空。report 可返回 progress、blocked、completed、failed、cancelled。
最终报告成功投递后，子方移除 received；父方收到后保留 awaiting_review，核验通过才 accept_report。
不通过时以同一主题 call 提交修订要求，重新登记子方任务。失败投递不能删除任务。
Worker 完成任务后仍监听。kill 才销毁子树。MailboxPort 的实现由 Hypervisor 提供，Runtime 不导入实现。

ConversationSession 将 mail 暂存后 ACK；输入在 checkpoint 历史出现后才从本地 pending 移除。
模型失败恢复不丢失已接收输入，重投 mail ID 不重复加入历史。Agent 空闲和运行中读取同一邮箱，
但所有 graph 更新都在所属 Worker 的安全模型边界发生。通信不抢占设备或正在生成的模型请求。
只有绑定 Gateway 的 Root 能 reply；子 Agent 的 talk 通过父任务进展报告间接传递。
