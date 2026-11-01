# Runtime Warnings and Memory Investigation

调查日期：2026-10-04，Asia/Shanghai。

历史记录说明（2026-10-11）：本文保留调查时的对象名、路径和实验结论；其中通信 proxy、inbox 与 FeishuAgentRunner 的描述已不代表当前实现。长期运行入口已改为 AgentWorker，由 AgentCoordinator 调度 AgentEngine；历史通信方案见 [归档](deprecated/runtime-before-hypervisor/channel-architecture.md)，新目标见 [三层设计](agent-system-design.md)。本文的内存实验未按新架构重跑。

范围：Bines 当前工作区、bines Python 环境、VS Code 调试配置及已安装依赖源码。
本次仅新增本文档，不修改运行代码、配置、依赖或已有采集数据；未启动真实模型请求或设备操作。

## 1. 结论

1. **Pydantic warning 的类型冲突已复现。** 三个工具仍声明裸 `ToolRuntime`，其默认 context 类型为 `None`，与新通信层注入的 `_Inbox` 不一致。相同输入改为 `ToolRuntime[Any]` 的独立对照实验没有 warning。当前不能把该 warning 等同于内存泄漏。
2. **MallocStackLogging warning 的产生机制已复现，但业务运行中的触发者尚未定位。** 它来自 macOS malloc 调试设施：请求关闭未启用的 stack logging。独立 Python 进程直接调用该系统接口会输出完全相同的文本并正常退出。它不表示 Python 已检测到堆损坏，也不能证明当时开启了昂贵的分配栈记录。
3. **存在与调试器无关的、可验证的无界历史保留。** 当前 `messages` 为 `BinaryOperatorAggregate`，`InMemorySaver` 为多个历史版本保存累计消息。小规模实验呈近二次增长，且 runtime 的 `close()` 不释放 checkpoint。
4. **新增通信接口存在已验证的生命周期保留问题。** 外部持有一个已关闭的 proxy，仍可通过 `_on_close` 闭包持有整个 runtime 及 checkpoint。
5. **尚不能将用户观察的 60 GiB 全部归因于某一个问题。** 没有当时的进程内存时间序列、heap snapshot、checkpoint 体积或调试器状态；本次进程快照中已没有相应高内存 Python 任务。已找到足以优先处理的增长机制，但尚未复现 60 GiB 峰值。

这里区分三类现象：

- 持续保留：对象仍被引用，GC 无法释放，包括 checkpoint 和持有 runtime 的闭包。
- 临时峰值：序列化、图片编码、树重建等造成多份临时表示同时存在。
- 原生泄漏：已失去业务用途、不可回收的 native allocation；本次没有证明存在此类泄漏。

## 2. 环境与证据边界

| 项目 | 实测结果 |
|---|---|
| Python | bines 环境，Python 3.14 系列 |
| langchain / langchain-core | 1.3.4 / 1.4.0 |
| langgraph / langgraph-checkpoint | 1.2.4 / 4.1.1 |
| pydantic | 2.13.4 |
| 调试器 | VS Code 扩展 `ms-python.debugpy-2026.6.0-darwin-arm64`；conda 内未单独安装 debugpy |
| `.vscode/launch.json` 的 test 配置 | `justMyCode=false`、`OPENAI_LOG=debug`、`NTRACE_ENABLED=true`，nTrace 指向本机 |
| 当前诊断 shell 的 malloc 环境 | 仅发现 `MallocNanoZone=0`，不是发生问题时 VS Code 子进程的环境证据 |

只读 `ps` 快照：当时可见的 Python PID 28085 RSS 约 58 MiB；用户日志中的两个 PID 已不在当前列表。内存最高的 VS Code 进程 RSS 约 1.13 GiB。
这不是对故障现场的测量，且 RSS 不等于 macOS Activity Monitor 的完整内存 footprint；VSZ 也不能当作实际物理占用。不能据此否认过去的 60 GiB。

没有附加调试器到正在运行的业务进程，没有启用全量 malloc stack logging，没有运行 `leaks` 或生成大堆转储。

## 3. Warning A：ToolRuntime context 类型不一致

### 已确认调用链

- `src/agent_runtime/_stability.py:24`：graph 的 `context_schema=_Inbox`。
- `src/agent_runtime/runtime.py`：模型和工具执行使用绑定会话的 `_Inbox`。
- 已安装 `langgraph/prebuilt/tool_node.py:106`：`ContextT = TypeVar("ContextT", default=None)`。
- `ToolRuntime.context` 使用该泛型参数。裸 `ToolRuntime` 并不表达任意 context。
- 已安装 `langchain_core/tools/base.py:711`：参数通过 `model_validate` 后调用 `model_dump()`。
- `pydantic/functional_validators.py:835` 位于 `SkipValidation` serializer：验证跳过不意味着序列化时不检查原类型预期，所以报 `Expected none`。

当前遗漏的工具声明：

| 位置 | 工具 |
|---|---|
| `src/strategy/structured/persistence.py:111` | `is_done(runtime: ToolRuntime)` |
| `src/middleware/probe/lifecycle.py:100` | `authorize_probe_completion(runtime: ToolRuntime)` |
| `src/tools/page_notes.py:8` | `update_page_notes(..., runtime: ToolRuntime)` |

`talk` 和 `prepare_collector` 已使用 `ToolRuntime[Any]`，不属于这三个遗漏点。
用户日志未包含具体工具名，因此能确认冲突机制和全部静态候选，不能确定日志中的某一行究竟对应哪一个工具。

### 隔离复现

在无网络、无设备、无 debugpy 的进程中：为两个签名仅在 `ToolRuntime` / `ToolRuntime[Any]` 上不同的函数分别创建 `StructuredTool`；构造携带真实 `_Inbox` 的 ToolRuntime，执行生成的 args schema 的 `model_validate(...).model_dump()`。

结果：裸类型出现与用户相同的 `PydanticSerializationUnexpectedValue(Expected none ... context ... _Inbox)`；显式 `Any` 为零 warning。

影响：告警噪声、额外格式化开销、工具注入契约不准确。没有证据表明 Pydantic 会因这条 warning 永久缓存每次 context，也没有证据能用它解释 60 GiB。

建议但未执行：统一这些隐藏上下文声明；增加在真实绑定 context 下序列化工具参数的回归测试，而不是全局屏蔽 warning。

## 4. Warning B：macOS MallocStackLogging

### 已确认与未确认

已确认它属于系统分配器的调试日志开关，不是 LangChain/Pydantic 的 Python warning。Apple 的公开 libmalloc 实现中 `turn_off_stack_logging()` 将调用委派给 MallocStackLogging.framework。

本次在一个独立小进程中运行以下诊断，未修改 agent：

```python
import ctypes
fn = ctypes.CDLL(None).turn_off_stack_logging
fn.argtypes = []
fn.restype = None
fn()
```

得到完全相同的 `python(<pid>) MallocStackLogging: can't turn off malloc stack logging because it was not enabled.`，进程正常退出。

但这只证明产生机制，不证明真实调用者：

- 在 `src`、当前 debugpy Python 源码和已检索的 Python 标准库源码中，未找到直接设置 MallocStackLogging 或调用上述接口的实现。
- `.vscode/launch.json` 没有设置 MallocStackLogging。
- 可能来自启动器、原生库、系统事件或子进程生命周期；没有 native stack，不能指定其中一个为根因。
- `src/device/adapter.py:191` 每次 ADB 操作创建子进程，但未验证用户日志的 Python PID 是否为 exec 前的子进程。不同 PID 不能直接证明模型创建了多个 Python agent。

该日志不能作为“应该开启 MallocStackLogging”的依据。为消除日志而开启全量分配栈记录，反而可能增加开销。

资料：[Apple malloc 调试说明](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/ManagingMemory/Articles/MallocDebug.html)、[Apple libmalloc 开关与 fork hooks 源码](https://github.com/apple-oss-distributions/libmalloc/blob/main/src/malloc.c)。

## 5. 内存与卡顿问题清单

### M1. 高优先级：累计消息的 checkpoint 近二次增长，已实验确认

位置：`src/agent_runtime/_stability.py:24`、安装环境 `langgraph/checkpoint/memory/__init__.py:429`。

所有 runtime 使用 `InMemorySaver`。其 `storage`、`writes`、`blobs` 保存各历史 checkpoint/version；当前工程没有调用线程删除或裁剪接口。`put()` 对变化 channel 的值进行序列化并保留历史 blob。

重要细节：当前 langgraph 包虽然已经有 delta channel 支持，但实测此工程创建的 `agent.channels['messages']` 仍为 `BinaryOperatorAggregate`，不能假设自动只保存消息差量。

无调试器、`NTRACE_ENABLED=0` 的实际 `create_runtime` 实验：假模型固定返回 `ok`，单会话每轮新增一条约 16 KiB 的 HumanMessage。每个采样点执行 GC；未请求真实模型。

| 轮数 | 最新历史消息数 | checkpoint 数 | blob 字节量，MB | pending writes，MB | tracemalloc 当前量，MB |
|---|---:|---:|---:|---:|---:|
| 10 | 20 | 90 | 2.011 | 0.168 | 3.801 |
| 20 | 40 | 180 | 7.381 | 0.336 | 9.861 |
| 40 | 80 | 360 | 28.202 | 0.671 | 32.046 |

MB 为十进制；blob 统计为所有存储 bytes 的总长度，不是进程 RSS。相较仅约 0.66 MB 的新增原文，40 轮已经保留约 28.2 MB blob。关闭 runtime 并 GC 后，blob 字节量不变。

若每轮新增数据为 s，且多个消息版本完整保存，累计可达到 O(s × n²)；XML artifact、截图和长工具结果会放大 s。实验说明这条路径无需 debugpy 即可持续膨胀；它不是 60 GiB 现场复现，也不应直接用小样本外推现场绝对值。

建议：首先定义 checkpoint 保留/恢复策略，将审计历史与热运行状态分离。评估增量存储、持久化 saver、历史 checkpoint 裁剪和大载荷引用。仅换数据库能转移常驻内存，但若仍全量保存所有版本，不会自动消除存储与序列化复杂度。

### M2. 高优先级：模型请求压缩不等于运行时历史压缩，已确认

位置：`src/strategy/context/compression.py:46`、`src/tools/page_bridge.py:56`、`:78`、`src/tools/event_hub.py:321`。

- middleware 只调用 `request.override(messages=...)`；不会删除 state.messages，也不会删除旧 checkpoint。
- PageBridge 的查询和设备操作 artifact 携带 `page_xml` 以及含 XML 的 `page_snapshot`。即使同一 Python 字符串对象被复用，序列化后的多个字段和多个 checkpoint 仍可能重复占空间。
- collector 的截图工具返回完整 base64 图片；它是 ToolMessage 内容，而不仅是磁盘路径。
- 保留最近三个页面操作，只对发送给 conductor 模型的投影有效，不能限制 graph 内存；collector 本身不执行此压缩。

建议：保留现有时间顺序和审计语义，但将原始图片/XML 存入证据仓库，历史使用可恢复引用；避免把减少模型 token 当成内存治理措施。

### M3. 中高优先级：关闭 proxy 后仍强引用整个 runtime，已实验确认

位置：`src/agent_runtime/runtime.py:60`、`src/agent_runtime/communication.py:53`、`:66`。

引用链：外部变量 → 已关闭 proxy → `_on_close` → `release` 闭包 → runtime → graph → checkpointer。
`close()` 移除 runtime 的会话绑定并清除 sender，但不清除 proxy 的 `_on_close`。

弱引用实验：创建 runtime 和绑定 proxy，关闭 proxy，删除 runtime 局部变量并 GC，runtime 仍存活；再删除 proxy 并 GC，runtime 才释放。

这不是所有关闭必然泄漏：外层及时丢弃 proxy 时可以回收。风险在于业务层保留 proxy、调试控制台变量或测试容器长期持有它，使本应释放的大量 checkpoint 一并滞留。

建议：关闭回调执行后断开所有权引用，明确 pending 消息在关闭/恢复时的生命周期；增加 weakref 生命周期回归。

### M4. 中优先级：runtime 的历史、会话与训练记录没有容量/寿命策略

位置：`src/agent_runtime/runtime.py:48`、`:239`、`src/page/mechanism.py:519`。

`sessions` 保留每个 session 最新完整消息列表，runtime.close 不清理它；训练 registry 按 visit 增长。Feishu 正常结束只移除通信会话，不会删除 graph thread/checkpoint 或 runtime.sessions。

`sessions` 中的列表主要是浅拷贝，不能把它直接按“完整内容再次深拷贝”计费；但它确实延长消息对象存活时间。训练 registry 相比大图片/历史 blob 通常较小，不宜列为 60 GiB 首要原因。

建议：区分运行中、待恢复、已完成会话；定义归档和释放契约，不能直接清空尚待恢复的数据。

### M5. 中高优先级：collector 全量历史和图片的重复处理

位置：`src/collector/history.py:26`、`src/collector/toolbox.py:74`、`:102`、`:265`、`:327`、`:459`。

- 每次 collector 创建时读取当前 probe run、当前 canonical path 的所有历史 record.json，包括内联图片、XML、baseline；不是所有旧 run 一起加载。
- `_stamp()` 将整个 `self.cases` 序列化为 JSON，再编码为 bytes 计算 SHA-256，图片也参与；产生可观的临时字符串/bytes 峰值和 CPU 开销。
- 每次 sniff_run 对全部用例生成 ground_truths、run_results 和 grades，工具结果进入 collector 历史，再被 checkpoint 多版本保存。
- `_save_cases()` 连同历史用例一起重写；随着用例增长，反复读取、编码、写盘都会加重卡顿。
- 每次委派新建 collector runtime；正常退出后局部 graph 理论上可回收，不能无证据宣称 manager 永久保存所有子 graph。调试器持有局部变量/异常栈时会延长存活。

磁盘观测：collector_history 共 168 个文件、326.53 MB，最大单文件 3.93 MB；`runs` 下 12 个 run，其中前三大的 run 的 record.json 总量约为 36.19/34.76/32.68 MB（19/17/15 条）。这些是磁盘大小，不等于实际加载量或 RAM。

建议：图片外置、历史按需加载、版本摘要不重复序列化图片、只写变化记录、回归结果避免反复把全表写进模型历史；不能降低“全部历史通过”的验证要求。

### M6. 中优先级：nTrace 有事件数上限，但缺少入队字节预算

位置：`nTrace/client.py:58`、`:70`、`:136`，`nTrace/events.py:20`，`nTrace/middleware.py:151`。

- 队列上限是 2000 个事件，不是 2000 个固定尺寸记录。完整模型上下文和工具 artifact 可使事件很大。
- 图片转换为路径发生在 worker 的 `_post()` 内，入队时仍可能保留内联图片。原文字符串可能被多个事件共享，不能机械地将事件数乘图片大小作为实际占用。
- `_post()` 先转换/构建 JSON body，再判断 8 MiB 原文和 512 KiB 发送限制并拆分；限制不是序列化之前的峰值内存上限。
- 网络失败重试或断点暂停消费线程时会增加积压；编码、gzip 与拆包还有临时分配。
- 已有边界：队列满时丢弃；known snapshots 上限 32768；默认 client 为单例。不是完全无限制的队列。

建议：按字节预算治理队列，在入队前轻量化大载荷、在完整序列化前分批，并观测队列字节量/积压与失败率。

### M7. 中优先级：调试可能显著放大保留和延迟，但尚未证明 debugpy 泄漏

位置：`.vscode/launch.json:22`；已安装扩展 `pydevd_suspended_frames.py:321`、`:409`、`:511`。

- 当前调试配置允许进入库代码，同时开启 nTrace 和 OpenAI debug 日志；“调试开/关”对照需要固定这些变量，否则无法归因。
- debugpy 暂停时会保存 frame/variable 引用，展开大型 state/checkpointer/cases 会增加对象保留和变量展示开销。源码也有 `untrack_all()` 清理，不能据此直接定性为永久泄漏。
- Watch、Debug Console 中主动保存的变量、异常断点及 VS Code 输出积累可能延长对象寿命；尚未获得用户当时的断点、Watch、console 和进程分布。
- Python 3.14 的实际调试实现支持 sys.monitoring，不能简单断言一定是逐行 `sys.settrace` 开销。

建议：相同输入用普通执行与 debugpy 执行作 A/B，分别测 agent、debug adapter、VS Code renderer 和 nTrace server；再单独切换 nTrace。不要只比较机器总内存。

### M8. 低到中优先级：Reco 删除节点墓碑持续积累

位置：`Reco/_sync.py:127`、`Reco/model_interface.py:69`。

sync 合并旧 `_deleted_ids` 和 `_deleted_aaids`，未见容量限制；当前有效树会替换，但删除 ID 的诊断记录保留到 PageSession 重建/重置。长期同页滑动可线性增长。单条墓碑小，不是已证明的主因。

建议：按 session/epoch 管理过期节点诊断，测墓碑数量与滑动次数关系；不能破坏有效公开 ID 的语义。

### M9. 中优先级：通信收件箱与业务任务队列无背压

位置：`src/agent_runtime/communication.py:42`、`src/feishu_agent_runner.py`。

收件箱为无上限 list；`ThreadPoolExecutor` 的待执行任务无字节容量策略。在模型阻塞、长时间待人工恢复或多会话涌入时会积累。父子 inbox 保存同一消息引用，不是每次深拷贝正文；子 inbox 在 finally 注销，正常消费按 ID 清理。

建议：先记录待处理条数/字节量和停留时间，再设计背压/磁盘暂存；不得静默丢弃已接受用户消息。本次没有消息洪峰证据。

### M10. 次要成本：模型统计、协议日志、树重建和 Excel 全量读写

- `src/codex/codex_oauth.py:630` 的 usage records 逐响应积累；有 reset 方法，但当前主运行路径未见周期调用。每条较小。
- `_request()` 同时持有 request、JSON payload 和消息输入直到响应完成，是大输入下的峰值因素；未发现模型对象持有一个无限增长的“所有请求/响应列表”。WS 独立线程不等于复制 Python 堆。
- 协议日志逐文件落盘，当前目录约 9.93 MB；它不是 Python 常驻缓存，但无归档策略会长期占盘。
- `ReadOnlyPageView` 重建 Reco 树、查询结果 deepcopy，`freeze_page()` 多次遍历区域树，属于临时分配/CPU 成本；没有证据说明每棵旧树永久保留。
- `CollectionWorkbookSink.persist_run_result()` 每批加载并保存整本 workbook；随数据增大加重 I/O 和瞬时内存。`_save_workbook()` finally 已关闭 workbook，不能误报为“没有 close”。
- `run.agent.py` 动态 module 没有插入全局 `sys.modules`；未发现按每次调用无限增长的模块缓存。

### M11. 附带安全发现：调试配置含明文凭证

`.vscode/launch.json` 中存在 API credential 字面量，部分在注释、部分在其他 launch 配置内。本文不复制值，也未验证凭证是否有效。
它不是内存原因，但应检查版本控制/日志传播范围并轮换仍有效的凭证；本次未修改配置。

## 6. 后续验证与处理优先级（尚未执行）

1. 统一 ToolRuntime context 类型，消除已定位 warning；不要用 warning filter 掩盖契约问题。
2. 优先治理 M1/M2：明确热历史、审计证据与 checkpoint 的分别存储和释放方式；回归消息配对、页面压缩、网络恢复和双向通信。
3. 修复 M3 的关闭后引用链，验证关闭 proxy 被保留时 runtime 可按设计释放；同时明确中断会话的存活要求。
4. 做固定用例、固定模型输出长度的 A/B：无 debugger/有 debugger，分别开关 nTrace，保持其他日志设置一致。每 10～20 个模型调用记录 RSS/footprint、checkpoint 数/字节、state 消息数/图片字节、cases 体积、trace 队列大小和通信待处理量。
5. 如果 checkpoint/cases/队列已稳定而 RSS 仍持续增长，再定位 native 分配和调试器引用；只在小样本上启用分配栈或堆采样，避免在 60 GiB 现场直接增加重型诊断开销。
6. 下次出现 malloc warning 时保存 PID/PPID、启动方式及经过筛选的 malloc/debug 环境变量；必要时在隔离复现中对系统关闭接口取 native stack，不能仅凭消息指定触发库。

验收应同时看“每轮增长斜率”和“会话结束后的可回收性”，不能只看 `gc.collect()` 是否执行、模型 token 是否下降或单次 RSS 是否下降。

## 7. 参考资料与限制

- [LangChain InMemorySaver API](https://reference.langchain.com/python/langgraph.checkpoint/memory/InMemorySaver)：明确将该实现定位为调试/测试，推荐生产使用持久化 saver。是否采用增量 channel 仍以本地实际 graph 为准。
- [Apple malloc 调试功能](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/ManagingMemory/Articles/MallocDebug.html)：说明分配栈记录、环境开关及相关工具。
- [Apple libmalloc 源码](https://github.com/apple-oss-distributions/libmalloc/blob/main/src/malloc.c)：开关委派和 fork hooks，用于识别警告所属层，不用来断言本机调用者。
- 当前结论以本地源码、安装依赖和隔离实验为主；未对正在运行的生产任务取堆，没有重跑真实商家采集，也没有复现用户的 60 GiB 峰值。
