# Tools 工具分类与引用关系

核对日期：2026-10-18。本文梳理按功能拆分后的 `src/tools` 实现及统一装配入口。分析区分三个概念：**定义了什么工具、谁负责注册、最终哪个 Agent 能调用**。目录中存在工具工厂，并不等于所有 Agent 都能使用它。

## 1. 目录职责

`src/tools` 主要承担业务能力到 LangChain StructuredTool 的适配。设备命令、页面树、Excel 存储和子代理运行逻辑分别由下层服务持有；工具层负责参数、描述、返回格式和策略 metadata。

| 功能分类 | 文件 | 主要职责 |
|---|---|---|
| 应用、手势与截图 | `device/operations.py` | 定义设备工具规格、选择角色工具集合并注册 |
| 语义导航 | `device/navigation.py` | 将页面内点击、进入页面、返回页面映射为设备操作 |
| Reco 页面访问 | `device/pages.py` | 直接采用 Reco TOOLS 定义的名称和 schema |
| 图片内容适配 | `device/screenshots.py` | PNG 内容编码、截图错误转换；不独立注册工具 |
| 人机交互 | `captcha/tools.py`、`reasoning/tools.py`、`communication/tools.py` | 验证码接管、think 与可选通信工具 |
| 自动通知 | `notifications/service.py` | 生命周期通知服务，直接注入 middleware |
| 页面到达确认 | `../strategy/tools.py` | confirm_page：绑定候选与观察引用，委托 PageVisitController 提交访问；由 ConductorEngine 注册 |
| 页面经验维护 | `page_notes/tools.py` | 更新当前页面 PAGE.md 中的操作观察 |
| 工作簿操作 | `workbooks/tools.py` | 包装 ExcelWorkbookService 的六项能力 |
| 通用子代理 | `subagents/tools.py` | 包装 SubagentManager 的创建、调用、移除 |
| 外部操作通知策略 | `notifications/policy.py` | 声明通知策略、服务协议和 metadata 读取接口 |
| 危险操作策略 | `shared/safety.py` | 风险等级、危险操作规则及 justification 数据模型 |
| 操作效果策略 | `device/effect.py` | 声明页面相似度的接受区间及其 metadata |
| 包入口 | `__init__.py`、`device/__init__.py` | 根包不负责装配；device 子包导出设备工具工厂和名称集合 |

这里有 **33 个不同名称的可注册工具**：通信与交互工具 4、设备操作 9、导航 3、Reco 页面工具 7、页面笔记 1、Excel 6、通用子代理 3。它们不是一个统一默认工具列表；后文按注册方式说明可见范围。

## 2. 工具清单

表中“参数”用于理解接口，完整类型、默认值和必填规则以实际函数及 Reco schema 为准。

### 2.1 通信与交互

| 工具名 | 定义位置 | 作用 |
|---|---|---|
| talk | communication/tools.py | 接收子类传入的发送函数，报告问题并返回发送状态 |
| authenticate_captcha | captcha/tools.py | 开启人工接管并通知用户，等待确认由 runtime/middleware 处理 |
| captcha_authenticated | captcha/tools.py | 用户确认后关闭人工接管 |
| think | reasoning/tools.py | 处理思考输入，不获取新信息或操作设备 |

AgentEngine 仅提供 `send_message(message)`，不注入或保留任何工具名称。ConductorEngine、CollectorEngine 主动使用 `create_talk_tool(self.send_message)`；SubagentEngine 允许提供自定义 talk，否则创建自己的 talk。新的派生类可以使用其他工具名、参数结构，也可以完全不提供通信工具。

OperationNoticeTool 迁至 notifications/service.py，作为自动通知服务直接注入 middleware；不再构造 notify_user 模型工具。ManualTool/query_manual、create_common_tools 和 build_tools 便捷包装已删除。

### 2.2 应用、手势与截图

定义：`ToolSpec` / `TOOL_SPECS`；工厂：`create_event_hub_tools`、`create_conductor_event_hub_tools`。

| 工具名 | 参数 | 下层调用/返回 | conductor 原始设备集合 |
|---|---|---|---|
| `run_package` | package_name、justification | DeviceOperationService.run_package；成功文本或错误 JSON | 是 |
| `close_package` | package_name、justification | DeviceOperationService.close_package；成功文本或错误 JSON | 是 |
| `tap` | x、y | 同名服务方法；页面内容及 artifact | 否，使用语义导航包装 |
| `swipe_up` | x、y | 同名服务方法；页面内容及 artifact | 是 |
| `swipe_up_long` | x、y | 同名服务方法；页面内容及 artifact | 是 |
| `swipe_down` | x、y | 同名服务方法；页面内容及 artifact | 是 |
| `swipe_to_random` | x、y | 同名服务方法；页面内容及 artifact | 否，显式选择时可构建 |
| `swipe_back` | 无 | 同名服务方法；页面内容及 artifact | 否，使用 navigate_back |
| `screenshot` | 无 | screenshot_content → DeviceOperationService.capture_png；image_url 列表 | 是 |

手势随机化、录像部署、设备锁与页面刷新不在工具包装层处理。应用工具验证 justification，并将 DeviceError 序列化为现有错误 JSON。手势工具使用 `content_and_artifact`，截图返回实际图片内容，不是设备文件路径。

注册细节：

- `create_event_hub_tools()` 默认返回 8 个原始设备工具（排除 swipe_to_random），并附加 7 个页面工具。
- 显式传入 `tool_names` 时只返回选中的设备工具，不自动附加页面工具。当前代码使用 `tool_names or EVENT_HUB_TOOL_NAMES`，因此空 tuple 会选中默认设备集合，但仍不附加页面工具；这不是“空集合等于无工具”。
- `create_conductor_event_hub_tools()` 固定选择表中标记“是”的 6 个工具。
- “collector-only”名称集合是注册约定，不是工厂内部的角色鉴权。实际 collector 使用自己的 `collector_swipe_to_random`，不直接使用这里的同名原始工具。

### 2.3 语义导航

对象：`NavigationTools`；工厂：`create_navigation_tools`。

| 工具名 | 参数 | 实际设备动作 | 语义约束的所有者 |
|---|---|---|---|
| `tap_in_page` | x、y、purpose | DeviceOperationService.tap | 当前页面内操作；purpose 区分普通操作与关闭遮挡 |
| `navigate_to` | x、y、next_path、merchant_name、recovery_reason | DeviceOperationService.tap | ProbeControlMiddleware 等校验目标路径、商家状态和恢复条件 |
| `navigate_back` | next_path、recovery_reason | DeviceOperationService.swipe_back | 业务 middleware 校验返回路径与离开条件 |

导航类保留模型参数，但不自行执行路径管理、商家去重或页面状态提交。navigate_to / navigate_back 的页面变化预期通过 metadata 声明。所有三项都返回页面内容和 artifact。

### 2.4 Reco 页面视图

工厂：`create_page_tools(PageBridge)`。工具定义由 `Reco.model_interface.TOOLS` 提供，桥接函数调用 `PageBridge.call(name, kwargs)`，将内容序列化为 JSON 文本，并保留页面 artifact。

| 工具名 | schema 参数 | 作用 |
|---|---|---|
| `page_view` | key、offset、limit | 页面/区域视图及分页 |
| `page_catalog` | offset、limit | 页面目录 |
| `page_read` | key、offset、limit、char_offset、max_chars | 区域内容与文本分页 |
| `page_node` | key（必填） | 单个节点信息 |
| `update_aaid` | id、value（必填） | 修改语义别名；这是写入操作，不是纯查询 |
| `select_aaid` | aaid（必填） | 按别名定位 |
| `node_update_ratio` | id（必填） | 节点变化量 / 旧子树节点数，上限 1 |
| `view_update_ratio` | id（必填） | 可视区域内绝对面积变化 / 旧可视面积，上限 1 |

除表中标注项外参数均非必填。页面捕获与缓存条件由 PageBridge 决定，工具层不重复实现。collector 使用相同 Reco 名称和 schema，但由 CollectorToolbox 包装，以支持冻结/历史视图限制，不能把这两个注册路径当作同一个工具对象。

### 2.5 页面经验

`create_page_notes_tool` 注册 `update_page_notes(content)`。ToolRuntime 由框架注入，工具从运行状态读取 PAGE_CONTEXT_PATH，调用 `PageMechanism.update_page_notes`，返回 `{ok, updated}`。没有当前路径时抛出错误。

该能力维护页面经验，不负责页面树查询，也不写采集数据，因此不属于 device 目录。

### 2.6 Excel 工作簿

工厂：`create_content_provider_tools`；同一次工厂调用创建一个 ExcelWorkbookService 并绑定六项工具。

| 工具名 | 主要参数 | 作用 |
|---|---|---|
| `create_excel_file` | relative_path、headers_json、rows_json、sheet_name | 创建工作簿 |
| `create_excel_sheet` | relative_path、sheet_name、headers_json、rows_json | 新建工作表 |
| `rename_excel_sheet` | relative_path、sheet_name、new_sheet_name | 重命名工作表 |
| `write_excel_sheet` | relative_path、sheet_name、headers_json、rows_json | 替换工作表行数据 |
| `append_excel_rows` | relative_path、rows_json、sheet_name | 追加行 |
| `update_excel_cell` | relative_path、cell、value、sheet_name | 修改单元格 |

文件路径约束、读写和结果由 tools.workbooks.excel 实现，工具工厂只设置工具描述与函数绑定。工作簿工厂供需要该能力的派生类显式选用；旧兼容装配分支已删除，主 conductor 不直接调用它们。conductor 的采集写入走 CollectionWorkbookSink 等业务服务，不能从这里的 Excel 工具列表推导采集持久化行为。

### 2.7 通用子代理

工厂：`create_subagent_tools(manager)`；绑定外部提供的 SubagentManager。

| 工具名 | 主要参数 | 作用 |
|---|---|---|
| `spawn_subagent` | name、agent_type、instructions、tool_names、max_iterations | 创建同步子代理 |
| `call_subagent` | agent_id、task | 同步运行现有子代理并等待结果 |
| `kill_subagent` | agent_id | 从注册表移除并丢弃保留上下文 |

这些工具不是 prepare_collector 的实现，也不属于默认 conductor 能力。相关描述已移除旧 query_manual 引用。

## 3. 唯一总装配入口

| 派生类 | 总入口 | 组装内容 |
|---|---|---|
| ConductorEngine | _create_tools | 验证码、think、talk、页面笔记、设备、Reco、导航、prepare_collector、is_done、collect_continuously、authorize_probe_completion |
| CollectorEngine | _create_tools | CollectorToolbox 的页面/训练工具，加本 Agent 的 talk |
| SubagentEngine | _create_tools | 已选择的任务工具与通信工具；已有自定义 talk 时保留 |

业务工具模块继续各自实现小型工厂。总入口决定最终工具集合和共享服务实例，不再通过全局注册器追加、覆盖、再过滤。ConductorEngine 直接创建唯一设备服务、页面机制和采集协调对象，供工具与 middleware 共用。

AgentEngine 只验证工具名称唯一并构建 graph，不添加工具。旧 src/tool_assembly.py、collect_tools、collect_conductor_tools、_with_collected_tools 和过时的保留名称列表已删除。业务 middleware 仍按工具策略和运行状态执行校验。

collector 的页面、截图和 collector_* 手势具有训练/冻结语义，仍由 CollectorToolbox 独立实现，不与 conductor 的同名工具对象混用。

## 4. 引用关系

### 工具到服务

| 工具/辅助模块 | 直接依赖 | 依赖用途 |
|---|---|---|
| device.operations | src.device、device.pages、device.screenshots、三类策略模块、tool_factory | 注册操作工具并附加策略 |
| device.navigation | src.device、operation_effect、tool_factory | 语义参数包装与导航效果策略 |
| device.pages | Reco TOOLS、src.device.PageBridge、StructuredTool | 沿用页面协议 |
| device.screenshots | src.device 的图片/错误类型、ScreenshotSource Protocol | 调用 capture_png 并编码图片；同时被 collector 和 collection_audit 直接使用 |
| captcha / notifications | src.device、common.event_log、shared.xml | 接管服务、自动通知与日志 |
| page_notes | PageMechanism、页面状态类型、ToolRuntime、tool_factory | 按当前页面维护笔记 |
| content_provider | tools.workbooks.excel.ExcelWorkbookService、tool_factory | Excel 操作适配 |
| subagents | SubagentManager、tool_factory | 子代理生命周期适配 |

普通工具通过 `src/tools/shared/factory.create_tool` 调用 StructuredTool.from_function，保留原函数签名用于 schema 构建。Reco 工具直接构造 StructuredTool，以外部 TOOLS schema 为准。

### 策略到 middleware

| 策略模块 | 生产者 | 消费者 | 含义 |
|---|---|---|---|
| operation_policy | 直接注入的通知服务；设备应用工具的 ExternalOperationPolicy；collector 的准备工具策略 | ToolExecutionPolicyMiddleware、OperationNoticeMiddleware | 自动生命周期通知；不是模型额外执行 notify_user |
| safety | 应用工具的 DangerousToolPolicy 和 DangerousActionJustification | DangerousToolMiddleware 及应用工具包装 | 校验危险操作理由并执行相应约束 |
| operation_effect | 设备滑动工具与导航工具 | DeviceOperationReliabilityMiddleware | 依据声明的相似度范围检查效果，不从工具名推导策略 |

当前导航策略要求相似度小于 0.5；滚动策略接受 0.05 至 0.95（包含端点）。这是效果检查约定，不是 Reco 的页面变化比例定义，也不是采集完成规则。

## 5. 公共依赖与已删除入口

- shared/factory.py：共用的 LangChain 工具构造和类型边界。
- shared/xml.py：通知与验证码工具共用的 XML 转义。
- shared/safety.py：危险操作声明；device/effect.py：设备效果策略；notifications/policy.py：自动通知协议。
- 通信发送统一走 Engine → ConversationSession → GatewayManager。ConversationInputMiddleware 只同步模型调用边界的输入；旧工具路由 ContextVar、bind_message_route、notify_current_call 和全局 send_message 包装已删除。
- 删除兼容总注册器、旧手册工具、通知模型工具，以及各模块无生产调用的 build_tools 转发入口。

## 6. 实现依据

[ConductorEngine](../src/conductor/engine.py)、[CollectorEngine](../src/collector/engine.py)、[SubagentEngine](../src/tools/subagents/manager.py)、[AgentEngine](../src/agent_runtime/engine.py)、[工具构造边界](../src/tools/shared/factory.py)。

## 7. 验证结果（2026-10-16）

装配与通信相关回归 63 项通过，包括自定义通信工具名称、无默认 talk、按 Engine 隔离的发送地址、模型调用间输入接纳以及长工具期间通知。Pyright 为 0 错误，Ruff、显式变量/动态访问检查、类型例外和导入环检查通过。

全 tests 结果为 **508 passed、5 failed、30 subtests passed**，无收集错误。剩余失败为两项 Codex 缓存预期、一项 conductor middleware 组合预期，以及两处页面目录缺少脚本文件；未将这些既有问题混入本次工具整理。

### 工作簿、系统日志与探测任务的归属

`src/tools/workbooks/excel.py` 提供 `ExcelWorkbookService` 和 `CollectionWorkbookSink`，同目录的 `tools.py` 负责模型工具注册。采集业务直接复用工作簿实现，不依赖工具注册入口。

`src/common/event_log.py` 提供系统事件日志 `WorkspaceEventLogger`，设备、通知及 middleware 共用。`common` 仅承载系统运维能力，不收纳业务任务。

应用探测的触发识别与输入上下文构造归入 `src/conductor/engine.py`，由 `ConductorEngine` 使用；提示内容及探测配置解析仍归 `src/prompts`。原 `src/storage` Python 模块已移除，不保留转发接口。

### 采集工具与共享执行服务

`src/tools/collection/continuous.py` 提供 `ContinuousCollection`，负责 `collect_continuously` 工具及连续采集循环。`src/tools/collection/service.py` 提供 `CollectionService` 和执行结果类型，统一单步采集、准入、工作簿写入与完成判断，供连续采集工具和 `CollectionPersistenceMiddleware` 共同调用。

`ConductorEngine` 仍是总装配入口，为工具与 middleware 注入同一个服务实例；服务不反向依赖 Engine 或 middleware。采集契约、共享数据类型与 collector 训练模块保持原归属。原 `src/collection` 源文件及说明已迁移，不保留转发模块。

### 共享能力的归属修正

工具的调用关系不等于所有权：子代理 Engine 和生命周期管理属于 `src/tools/subagents/manager.py`，`tools/subagents` 同时保存管理器与工具包装。页面级采集契约位于 `src/page/collection_contract.py`；页面指南归 page，评分和训练恢复归 collector，工作簿写入结果归 workbooks。采集执行模块只保留其完成与准入结果。通用 LangChain 消息与工具响应联合类型统一位于 `src/agent_messages`，不属于 channel 通信或某一工具。

## 页面策略准入

工具保持固定注册，由 ConductorEngine 注入的页面策略强制校验适用性。general_operation 不接受 prepare_collector、is_done 或 collect_continuously；structured_office 在等待训练时禁止产品操作。导航成功只建立候选，page_view/screenshot 获取观察引用，confirm_page 才提交新访问。详见 [页面策略架构](page-strategy-design.md)。

子代理的生命周期与执行器实现和工具工厂同属 tools/subagents（manager.py、tools.py），不再跨 src/subagents 引用。
