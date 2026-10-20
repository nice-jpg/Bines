# Python 强类型重构实施记录

本次重构尚未完成验收。范围为 Git 跟踪的 `src` 自有 Python 及拆分后新增的 Python 模块；C、忽略的第三方示例、页面 `run.agent.py`、录制文件和二进制不在范围内。

## 已实施的结构调整

- 统一 `src.*` 模块身份，删除业务代码的顶层导入兼容分支和星号导出。
- `DeviceOperationService` 位于 `src/device/operations.py`；设备工具声明保留在工具层。
- 页面桥接、页面变化提示、截图和日志分别归属于页面、设备和存储模块。
- Excel 服务与采集工作簿存储移至 `src/tools/workbooks/excel.py`，工具文件只负责注册。
- `PageMechanism` 委托 `PageDocumentStore` 和 `PageScriptExecutor`；训练记录独立到 `src/collector/training.py`。
- `CollectorValidationSession` 统一管理冻结快照、基准、评分和回归失效状态。
- 页面快照、训练结果、脚本结果、采集状态、完成判断和持久化结果已有明确类型。
- `DeviceError` 通过显式 `as_dict()` 保持工具错误 JSON；工具回调结果统一为 `ToolMessage | Command[str]`。
- 长期运行入口为 `LongRunningAgent`，仅接受 `completion_driver: AgentCompletionDriver`；Driver 负责单项任务持续推进，Runtime 负责图执行。通信能力通过 `AgentExecutionContext` 注入，队列及关闭由 ChannelGateway 封装，详见 [Channel 架构](channel-architecture.md)。
- 飞书事件解析使用 SDK 具体字段；WebSocket 使用 `ClientConnection` 和 `State`。
- 页面上下文、手册、历史消息压缩、探测完成结果及子代理调用已补充具体类型；手册 metadata 常量有统一归属。
- Codex WebSocket 事件、响应项和用量字段在适配边界进行验证。
- 仅测试使用的工作簿读取辅助函数已移至测试目录。
- 工具声明通过 `create_tool` 保留原调用签名，删除函数元数据及签名的动态修改。

## 已删除的默认接口

生产调用审查后已删除：配置加载的 `env_path`、模型工厂的 `temperature/token_provider/base_url`、动作同步目录、动作解析时间缩放与延迟上限、设备动作目录与输入设备、部分工具工厂注入参数、Agent 默认提示词，以及飞书客户端的测试专用注入参数。对应测试改为替换内部依赖。

`AgentRuntime._prepare_messages` 不再接收未使用的 `session_id`。无调用的通知器 `set` 方法已删除。

2026-10-10 继续沿生产调用链删除 43 个默认参数，依据已登记到 `checks/default_parameter_decisions.json`。涉及飞书通道和去重、日志目录、设备构造及动作目录、Excel 和采集存储、页面机制和历史目录、初始消息与探测任务、通知工具、OAuth 路径和训练协调。测试改用模块配置替换或具体字段设置，保留目录隔离、冷缓存读取、LRU、通知和设备命令断言。日期仍在每次构建消息时求值；睡眠函数仍在模块加载时绑定。

当前还有 159 条默认参数声明（含重载），其中 148 条已有生产用途或模型、框架、页面脚本契约依据，11 条待审查。检查同时确认本轮删除的 43 个参数没有重新引入。剩余项集中在 `run_agent_loop`、`create_collector_tools`、`SubagentManager.__init__`、`collect_tools` 四个旧入口，不能仅因测试使用或公开导出就宣称存在生产用途。

## 可重复执行的检查

在 Python 3.14 的 `bines` 环境运行：

```sh
python checks/python_style.py
python checks/import_graph.py
python checks/default_parameters.py
python checks/default_parameters.py --check
python checks/type_exceptions.py
pyright --pythonpath "$(command -v python)"
ruff check src
ruff format --check src
NTRACE_ENABLED=false python -m pytest -q tests
```

- AST 检查报告未注解的首次赋值、字段和函数声明，以及禁用的动态访问和星号导入。它没有历史问题豁免，未完成整改时返回失败。
- 导入检查区分纯类型导入，纳入跨包导入触发的包初始化依赖，报告循环和顶层模块身份混用。
- 默认参数清单排除各目录下的 tests/checks，包含生产调用候选和逐参数决策。同名方法候选不作为自动保留证明。`--check` 遇到未审查项返回失败，已删除参数重新出现也会失败。
- Pyright strict 覆盖全部范围内模块；未关闭整个文件或目录的类型诊断。

## 第三方类型边界

| 符号 | 原因与限制 |
| --- | --- |
| `tool_factory.create_tool` 的 `StructuredTool.from_function` 调用 | LangChain 对同步 callable 的返回值声明不完整；仅忽略该成员的 unknown 声明，调用方使用 `ParamSpec` 和返回值泛型。 |
| `message_text.message_content` / `message_additional_kwargs` 的 SDK 字段读取 | LangChain 内容块和附加 metadata 使用裸字典；仅在读取边界接受未知值，后续显式验证文本、有限 JSON 或具体 metadata 字段。 |
| `agent_runtime.types.framework.RuntimeMiddleware` | LangChain 的状态、上下文泛型不变，组装时需要容纳不同 middleware schema；此处的 `Any` 仅描述框架组装，业务状态使用具体类型。 |
| `message_types.InputMessage` 的 SDK 消息字典 | 对齐 LangChain `create_agent` 接受的消息表示；业务解析不依赖该字典中的模糊字段。 |
| `middleware.tool_io.ToolInvocation.config` | `ToolCallRequest.runtime` 丢失状态泛型；仅忽略这一属性读取的 unknown，返回 SDK 明确声明的 `RunnableConfig`。 |
| `typings/Reco/model_interface.pyi` | 当前安装的 Reco 公共查询接口补充声明，不替换运行时代码。 |
| `typings/codex_apply_patch/__init__.pyi` | 当前安装的 0.4.0 Python API 无声明；已核对内存补丁返回值和解析结果，无 C 构建或回放。 |

按导入或读取符号登记的完整列表见 `checks/type_exceptions.json`，其中也记录 Lark SDK 与 LangGraph 的 inline 类型声明缺少 stub marker、SDK `create_agent` / `invoke` 裸泛型及工具 schema 转换的限制。`checks/type_exceptions.py` 拒绝未登记、扩大、失效或整文件豁免。

`checks/any_boundaries.json` 额外登记四条 SDK `Any` 声明：`InputMessage`、`RuntimeMiddleware`，以及 `OpenAICodexModel.bind_tools` 的 tools/tool_choice 两个参数。检查拒绝新增的未登记 `Any`，包括别名导入和 `typing.Any` 访问；业务层不再使用 `Any`。

## 既有失败基线

重构前基线为 439 passed、8 failed，另有 32 个子测试通过。以下既有失败尚未改动其业务行为：

| 失败 | 原因及当前处理 |
| --- | --- |
| Codex 工具绑定请求缓存键测试 | 期待调用方传入的缓存键，现实现按 session ID 发出；保留当前协议，待独立核对测试契约。 |
| Codex 缓存键实例属性测试 | 期待旧 `prompt_cache_key` 实例属性；当前模型使用 session ID。 |
| Android 支持动作测试 | 期待旧 `touch/act` 列表，当前公开动作为 `tap/swipe_*`。 |
| conductor middleware 组合测试 | 期待旧手册包装路径；当前手册在 before-model 阶段持久化。 |
| 模型配置文件测试 | 测试文件写入 `DEEPSEEK_API_KEYs`，实现读取 `DEEPSEEK_API_KEY`。 |
| 未知模型 provider 测试 | 期待固定 provider 白名单，当前实现按 provider 前缀读取配置。 |
| 页面文档：外卖/美食 | 断言旧 `run(xml: str)` 文本；忽略的页面脚本实际为 `run(view)`，不修改该脚本。 |
| 页面文档：外卖/美食/商家 | 同上，不修改排除对象。 |

新增类型边界测试覆盖无效状态、快照复制、消息文本校验和 Command 扩展字段保留。最终统计应以实施结束后的全套结果为准，不能将上述中间基线视为验收通过。

## 未完成项

2026-10-10 已验证：107 个范围内 Python 文件的 Pyright strict 为 0 errors；AST 变量、字段、函数声明及禁用访问检查为零问题；Ruff 代码和格式检查通过；导入图没有运行时环路或双重模块身份。21 条第三方诊断例外和 4 条 SDK Any 声明均通过登记检查。

本轮全量测试：447 passed、8 failed、32 个子测试通过。失败项与上表基线一致，没有新增回归。Python 设备测试仅验证命令构造和包编码，没有编译或执行 C 回放。Git diff 中 C、native、动作录制及 run.agent.py 均未变化。

尚需完成 11 条旧入口默认参数的决策、剩余同义字符串常量归并、架构文档同步及逐文件人工验收。静态类型通过不等于整个计划已经完成。
