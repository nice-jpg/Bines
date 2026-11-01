# src 文件职责与依赖归属

本文保留此前目录整理记录，其中子 Agent 和通信目录已发生变化。
当前 Channel/Hypervisor/Runtime 分层见 [三层架构](agent-system-design.md)，
通信和 Agent 装配以该文档及当前源码为准，下文保留其他业务目录的整理记录。

本次整理将原先直接放在 `src` 下的实现归入实际功能模块。目录归属依据调用方和职责，而不是文件名称中的 `types`、`state` 或 `manager`。迁移保持类名、工具协议、消息结构和业务策略不变；内部导入统一使用新路径，不保留旧路径转发文件。

## 根目录

`src/__init__.py` 仅声明包；`requirements.txt` 保留依赖清单；`run_agent.py` 是可直接执行的 CLI 入口，负责应用启动和关闭。模型创建和业务 Engine 实现不放在入口文件中。

## 文件归属

| 原根目录文件 | 当前路径 | 实际职责与主要调用方 |
| --- | --- | --- |
| agent.py | conductor/engine.py | ConductorEngine、探测输入准备和统一工具装配；应用入口与 trainer 使用 |
| probe_state.py | strategy/state.py | 探测进度、商家完成记录和采集状态；Engine、工具及 middleware 共用 |
| state_types.py | strategy/state.py | Conductor 的组合状态与读取验证；不是通用 runtime 状态 |
| collection_audit.py | tools/collection/audit.py | 采集证据抽样；CollectionService 使用，不属于系统运维日志 |
| collection_contract.py | page/collection_contract.py | 字段准入、完整性与采集契约；页面机制、collector 和工作簿服务共用 |
| collection_types.py | page/collection_types.py、collector/types.py、tools/workbooks/types.py、tools/collection/types.py | 按页面指南和评分、训练恢复、工作簿写入、采集完成与准入分别拆分 |
| subagents_manager.py | tools/subagents/manager.py | 子代理记录、生命周期及 SubagentEngine；子代理工具使用 |
| tool_types.py | agent_messages/tool_results.py | LangChain ToolMessage / LangGraph Command 结果联合类型；与消息结构同属框架对话能力 |
| page_mechanism.py | page/mechanism.py | 页面机制门面、脚本调用和结果评估；collector、页面笔记及采集服务使用 |
| page_documents.py | page/documents.py | 页面文档分节读写；PageMechanism 使用 |
| page_scripts.py | page/scripts.py | 页面脚本加载、入口与输出校验；PageMechanism 使用 |
| script_types.py | page/script_types.py | 页面脚本执行和补丁结果结构；页面与采集模块共用 |
| page_paths.py | page/paths.py | 页面逻辑路径规范化；页面机制和上下文处理使用 |
| page_state.py | strategy/state.py | 页面上下文状态及键；Conductor 状态组合、工具和 middleware 使用 |
| page_message_context.py | strategy/context/messages.py | 消息附带的页面上下文结构与读取；页面上下文 middleware 使用 |
| message_text.py | agent_messages/text.py | LangChain 消息正文、metadata 读取；Engine、工具与模型适配复用 |
| message_types.py | agent_messages/types.py | LangChain 输入、图输入和历史消息结构；业务与 runtime 平级复用，不是 channel 消息 |
| model.py | application/model.py | 根据配置装配和关闭模型；应用入口、trainer 和 mind_controller 使用 |
| raw_run.py | application/raw_run.py | 控制台运行及采集结果评分；trainer 动态加载，调试入口同步迁移 |
| paths.py | common/paths.py | 仓库、workspace 及运行文件位置；配置、日志和业务存储使用 |
| json_types.py | common/utils/json.py | 递归 JSON 类型及输入校验；不依赖业务模块 |
| framework_data.py | common/utils/validation.py | 未知输入的映射与序列类型收窄；框架边界与脚本边界复用 |

## 模块边界

`conductor.engine` 负责装配，工具、middleware 只引用 `conductor.state` / `conductor.probe_state` 的数据定义，不反向调用 Engine。`conductor/__init__.py` 不聚合导出 Engine，避免只读取状态时加载整个业务装配链。

`page` 是多个业务能力共用的页面领域模块。页面笔记只是它的一个调用方，因此不把整个页面机制放进单个工具目录。设备模块继续负责实时 Reco 视图，`page` 负责页面文档、脚本和业务上下文。

`agent_messages` 是 LangChain 对话数据的共享模块：`types.py` 定义框架输入和历史消息，`text.py` 读取正文与 metadata，`tool_results.py` 定义工具响应联合类型。业务 Engine、middleware、模型适配和 runtime 都可以直接依赖它。该模块不依赖 runtime，不负责信道、会话路由或收发队列。

`agent_runtime/messaging` 只表示 channel 传输信封 `IncomingMessage` / `OutgoingMessage`、队列和流关闭语义。它与 `agent_messages` 不是同一种消息；ConversationSession / 输入 middleware 是将收到的通信文本转换成模型输入的边界。不能因为两者都称为 message 就合并目录。

`page/collection_contract.py` 定义页面级采集契约，与页面资产一起构成字段完整性和身份规则。页面机制、collector 和工作簿服务都是其消费者。原 `collection_types.py` 不再作为混合结果容器：页面拥有指南和脚本评分，collector 拥有训练与恢复结果，工作簿模块拥有落盘结果，采集执行模块拥有完成与准入结果。页面和 collector 不再为了读取这些定义而依赖采集工具实现。

`tools/subagents/manager.py` 拥有 SubagentEngine、记录和生命周期；同目录 tools.py 包装模型可调用接口，相关实现集中维护。

`common/utils` 提供 JSON 数据及未知值校验，不依赖 Agent、channel 或业务模块。`common` 的其余部分保留系统运行路径与事件日志。页面消息 metadata 仍在 `strategy/context/messages.py`：它描述页面业务内容，使用 agent_messages 读取消息字段，但不是通用消息定义。

## 原根目录文件复核结论

以上表格覆盖原根目录全部 Python 实现。此次调整消息、采集契约、混合结果类型和子代理管理的归属；其余保留理由如下：

- `agent.py` 的装配保留在 conductor；原 probe_state.py、state_types.py 和页面状态统一归 strategy/state.py，不属于 runtime。
- 页面文档、脚本和契约保留在 page；策略状态与模型输入上下文归 strategy，设备实时视图由 device 负责。
- `collection_audit.py` 仅为采集执行提供证据抽样，保留在 tools/collection；它不是系统日志。
- `model.py` 和 `raw_run.py` 分别负责应用模型配置选择、控制台运行和评估，保留在 application，不属于可复用 runtime。
- `json_types.py` 和 `framework_data.py` 的实现只验证 JSON、映射和序列，归 common/utils；后者的旧名称不代表它只能服务 Agent 框架。
- `paths.py` 中仓库与 workspace 路径归 common，采集工作簿文件名归 workbooks。CLI、依赖清单和包声明保持根目录位置。

## 迁移验证

同步生产导入、测试补丁目标、动态导入、类型声明、检查记录和调试路径。`common/paths.py` 与 `page/mechanism.py` 的资源定位按新目录深度调整，workspace 与页面资产仍指向原位置。C 源文件、动作录像及页面脚本资产不变。

## Middleware 功能分组

`middleware/probe` 保留全局探测生命周期；`middleware/execution` 保留危险校验与通知。页面专用组合位于 strategy；跨组适配保留在 middleware/tool_io.py。状态直接引用 strategy/state.py，ToolResult 引用 agent_messages。详见 [middleware-catalog.md](middleware-catalog.md)。

## 当前页面策略与基础工具归属

| 所有者 | 内容 | 边界 |
| --- | --- | --- |
| strategy/state.py | 页面、探测和采集状态的 TypedDict schema；按功能分组的字段、策略名和阶段值 | 读取/收窄统一在此处；旧 checkpoint 重复字段在读取时转换 |
| strategy/base.py、middleware.py | 通用 hook 契约与委托 | 不依赖 conductor |
| strategy/general | 通用操作、访问确认、可靠性和验证码 | 不实施 collector 阶段控制 |
| strategy/structured | 结构化流程与采集消息适配 | 复用 collector/collection 领域服务 |
| strategy/context | manual 注入、请求投影和页面消息元数据 | 独立于 channel 消息 |
| strategy/declarations.py、tools.py | 策略声明与确认接口 | Engine 负责注入/注册 |
| common/utils | JSON 校验/比较、通用类型验证、XML 转义、文本指纹 | 无 page、strategy、tools 或 Agent 依赖 |
| page | 页面路径/别名、手册存取、采集契约和脚本加载/修改 | 路径含应用别名语义，因此不是通用 utils |
| collector/evaluation.py | 采集结果评分及 EvaluationGrade | 不是页面存储职责，也不是通用 JSON 工具 |
| tools/subagents | 子代理管理、Engine 与工具工厂 | 不保留 src/subagents 转发目录 |

原 src/serialization 全部为通用校验能力，已并入 common/utils。原 page/strategy、conductor/pages、middleware/context 和页面状态分散定义已移除，不保留双套接口。所有调用方及类型存根采用新路径。
