# Codex 适配器的 Responses 协议覆盖范围

核对日期：**2026-10-14**。依据 OpenAI 官方文档与当前工作区源码，补充 [模块架构](codex-architecture.md)。本文记录已实现能力及限制，不代表新增功能已获批准或已实现。

## 1. 结论与判定口径

当前适配器覆盖的主要调用链是：文本/图片输入、函数或 custom 工具调用、最终文本与用量返回，以及同一连接上的串行多轮续接。服务端事件被收集成一个 AIMessage；调用者不能通过本模块获得逐 token 输出。

最需要关注的缺口是：未知请求选项被静默过滤；错误原因、拒答、引用和消息阶段丢失；缓存 ttl 与公开协议不符；没有完整输出条目回放、内置工具结果适配和多路事件路由。

| 标记 | 含义 |
| --- | --- |
| 已支持 | 在表中限定的数据形态下，具备明确的构造或消费路径；不等于所有模型、端点均已联网验证 |
| 部分支持 | 已覆盖一部分字段/阶段，但缺少配置入口、结果保真或恢复语义 |
| 未支持 | 没有相应路径，或输入会被拒绝、过滤，输出会被丢弃 |
| 协议差异 | 已发送/消费的形态与公开文档存在差异，需要独立验证或修正 |

**端点边界：**代码默认连接 `wss://chatgpt.com/backend-api/codex/responses`，使用本地 Codex 凭据和专用连接头。官方 WebSocket 指南描述的是 `wss://api.openai.com/v1/responses`。本文以公开协议作为能力参照，不推断两者认证、可用模型、参数或扩展行为完全一致。修改 ws_url 不构成公共 API 兼容性验证。[官方 WebSocket 指南](https://developers.openai.com/api/docs/guides/websocket-mode)

## 2. 请求配置覆盖

实现依据：[请求类型与选项校验](../src/codex/protocol_types.py)、[请求构造与工具绑定](../src/codex/codex_oauth.py)。当前单次调用仅识别 `tools`、`tool_choice`、`parallel_tool_calls`、`previous_response_id`、`store` 五个选项。模型名、instructions、reasoning_effort 来自实例字段。

| 能力/字段 | 程度 | 当前范围与限制 |
| --- | --- | --- |
| `model`、`instructions`、`input` | 已支持 | 由模型字段和 LangChain 消息构造；不是任意原生 Responses input 的透传入口 |
| `tools`、`tool_choice` | 已支持 | 函数工具及选择策略转换；原生字典可透传，但工具结果支持范围另见第 3 节 |
| `parallel_tool_calls` | 已支持 | 默认 True；允许一个响应含多个调用，不表示多个模型请求并发，也不负责执行工具 |
| `store` | 已支持 | 默认 False，可单次覆盖；保存服务端返回的 store 标记 |
| `previous_response_id` | 部分支持 | 有增量续接与重连全量回放；没有按服务端错误码自动恢复无效锚点 |
| `reasoning` | 部分支持 | effort 使用实例配置；summary 固定 detailed，effort 为空字符串时为 auto；不提供完整 reasoning 对象入口或模型能力校验 |
| `include` | 部分支持 | 有 reasoning 时固定请求 reasoning.encrypted_content；不能选择其他额外返回字段 |
| `prompt_cache_key/options` | 协议差异 | key 固定使用 session_id；工厂同名参数被忽略；options 仅在有续接 ID 时生成，详见第 5 节 |
| `max_output_tokens`、`max_tool_calls`、`temperature`、`top_p`、`truncation` | 未支持 | 不进入请求体；不得把接收超时或工具执行限制当作这些服务端参数 |
| `text.format`、`text.verbosity` | 未支持 | 没有原生 JSON Schema / JSON mode / verbosity 请求映射 |
| `metadata`、`service_tier`、`top_logprobs`、`safety_identifier`、`prompt` | 未支持 | 无请求映射；不能通过 kwargs 启用 |
| `conversation`、`background`、`context_management` | 未支持 | 不接入相应服务端会话、后台任务或自动压缩能力 |
| WebSocket `stream_id`、`generate` | 未支持 | 不启用多路复用或 generate=False 预热 |

公开 Responses 的生成参数和响应结构见 [Create response](https://developers.openai.com/api/reference/typescript/resources/responses/methods/create)。参数能否用于具体模型仍需遵循该模型约束。

结构化输出通过 `text.format` 配置，与函数工具的 strict schema 是不同入口。当前能传递函数 strict，并不能据此声称原生结构化文本输出已支持；LangChain 若通过工具调用模拟结构化结果，也属于框架能力。[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

**静默过滤已实际复核：**向 read_request_options 传入 max_output_tokens、text.format、stream_id、store 后，仅留下 store。调用没有报错不代表参数已生效。

## 3. 消息和工具覆盖

| 输入/工具 | 程度 | 当前范围与限制 |
| --- | --- | --- |
| 用户文本与混合文本块 | 已支持 | 保持文本/图片块顺序；普通字符串不重新解释为 JSON |
| 图片输入 | 已支持 | image_url 转换、原生 input_image 的 URL 或 file_id、detail 字符串；没有上传文件、校验图片格式或校验 detail 枚举的能力 |
| SystemMessage | 已支持 | 开头连续系统消息并入 instructions；后续系统消息转 developer 并保留位置 |
| 助手历史文本 | 部分支持 | 普通字符串回放为 output_text；AIMessage 列表内容经 JSON 序列化变成文本，不能无损回放富内容 |
| 文件、音频、视频输入块 | 未支持 | 当前转换器只接受文本和图片；其他块报 ValueError。此项描述本地边界，不表示所有模态都属于同一 Responses 端点能力 |
| 函数工具声明 | 已支持 | 常用 name、description、parameters、strict 转换；不验证返回参数是否符合 schema |
| 函数调用与结果 | 已支持 | call_id 关联、JSON 参数、文本及文本/图片块结果；文件结果未支持 |
| 函数高级声明字段 | 部分支持 | 原生 Responses 字典可保留额外字段；Chat Completions 的嵌套 function 转换仅复制上述常用字段，没有高级调用模式调度 |
| custom 工具 | 部分支持 | 声明/grammar 透传，输入转为 `__arg1`，调用标识随历史保存；结果只识别项目约定的 custom_tool_call_output 内容块，并转成字符串 |
| Web search / File search | 部分支持 | tools 字典有透传路径，最终普通文本可被读取；搜索动作、结果结构、引用不保留，未验证服务端可用性 |
| Computer use / Shell / Apply patch / MCP | 未支持完整调用链 | 无专用调用、审批、结果回传适配；仅透传声明不能让本地执行器处理这些原生调用 |
| Code interpreter / Image generation | 未支持完整结果 | 不保留专用执行结果、文件和图像产物；可能只留下伴随文本 |

函数工具的一般闭环为声明、模型返回调用、应用执行、提交关联结果；strict 约束也需要模型与 schema 满足要求。当前模块只负责其中的协议转换。[Function calling](https://developers.openai.com/api/docs/guides/function-calling)

公开条目模型中的 custom_tool_call_output 可包含文本、图片或文件结果；当前的字符串化不能覆盖这些结构。不要把 custom grammar 声明保留测试理解成所有 custom 结果都可回放。[Input item reference](https://developers.openai.com/api/reference/resources/responses/subresources/input_items/methods/list)

## 4. 响应与事件覆盖

官方事件区分生命周期、输出条目、内容增量、工具参数、拒答和错误等类型。下表按能力分组，不用事件名称数量计算“兼容百分比”。[Streaming event reference](https://developers.openai.com/api/reference/resources/responses/streaming-events)

| 事件/结果 | 程度 | 当前消费方式与缺口 |
| --- | --- | --- |
| `response.output_text.delta/done` | 已支持 | 按 output_index/content_index 汇总；done 覆盖累计文本 |
| `response.output_item.added/done` | 部分支持 | 按索引/ID 保存条目；保留字段受 ResponseItem 限制 |
| `response.completed` | 已支持 | 优先使用最终 output；为空或缺失时回退累计条目/文本；转换为一个 AIMessage |
| `response.created/in_progress`、内容块生命周期 | 未支持独立语义 | 通常忽略，不建立完整响应状态机 |
| 函数参数/custom 输入增量事件 | 部分支持最终结果 | 不拼接此类 delta；依赖完整 output_item 或 completed.output 中的 arguments/input |
| reasoning 输出 | 部分支持 | 只保留带非空 encrypted_content 的 reasoning 项及其 summary/content；单独摘要和推理增量不向外提供 |
| `response.failed/incomplete`、`error` | 部分支持 | 可终止等待并抛 RuntimeError；error、status、incomplete_details 未在类型中声明，校验后丢失原因 |
| 拒答 `refusal` 及其事件 | 未支持 | 不处理拒答增量，最终 refusal 字段也被过滤；纯拒答可能产生空文本 AIMessage |
| annotations、logprobs | 未支持 | 文本可留下，但引用位置、来源、概率等信息丢失 |
| 消息 `phase`、条目 status/role | 未支持保真 | 输出转换未保留，无法区分 commentary/final_answer；多个文本条目合并为一个字符串 |
| 用量与 response_id/store | 已支持基本字段 | 返回输入/输出/总 token 及详情、续接标记；实例累计仅覆盖成功收到的调用，不能作为完整账单 |
| `sequence_number`、`stream_id` | 未支持 | 不追踪事件序号、不按并发响应分流 |
| 内置工具进度、产物、compaction | 未支持 | 无专门消费与历史保存路径 |

`response.text.delta/done` 与 `response.done` 是代码保留的兼容别名；本文不将其认定为官方标准事件。未知事件大多被忽略，但若与已声明字段冲突仍可能校验失败，不能承诺无条件向前兼容。

接收服务端事件流与对外提供流式接口不同。当前没有 _stream/_astream 实现，run_manager 未用于逐 token 回调；也没有 HTTP SSE 客户端。[Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses)

当前官方推理指南说明，stateless 模式可返回 encrypted_content，旧 include 值仍被兼容。现有实现可保存这一类项，但不保存所有输出类型，因此不能视为通用无损历史回放器。[Reasoning](https://developers.openai.com/api/docs/guides/reasoning)

以下为本次使用真实 TypeAdapter、合成输入复核的结果，无网络调用：

| 输入中的字段 | 校验后 |
| --- | --- |
| error 事件中的 status/error.code/error.message | 仅剩 type=error |
| incomplete 响应中的 id/status/incomplete_details | 仅保留 id |
| refusal 内容块中的 type/refusal | 仅保留 type |
| output_text 中的 text/annotations | 仅保留 type/text |

这也意味着完成响应日志写入的是校验后的事件，不能依靠该日志恢复已过滤的字段。

## 5. 续接、缓存与连接能力

| 能力 | 程度 | 当前行为及影响 |
| --- | --- | --- |
| 持久 WebSocket | 已支持 | 懒启动传输线程，复用连接，心跳与超时管理；每个实例串行处理请求 |
| `previous_response_id` 增量输入 | 已支持限定路径 | 历史锚点属于当前连接或明确已存储时自动使用；只发送锚点后消息 |
| 重连全量恢复 | 部分支持 | 未存储锚点在新连接上自动回退；遇到服务端 previous_response_not_found 不会按错误码再次降级 |
| 同连接多路并发/分支 | 未支持 | 没有 stream_id 与按 lane 保存的状态；parallel_tool_calls 无法替代此能力 |
| Mid-turn steering | 未支持 | 不发送 response.steer，也不处理相应确认/后继响应；所有 incomplete 均作为失败 |
| 预热 | 未支持 | 无 generate=False 入口 |
| 缓存 key | 部分支持 | 固定实例 session_id；工厂 prompt_cache_key 参数无效 |
| 缓存期限 | 协议差异 | 实际发送 ttl="30ms"；不按模型选择缓存参数，也不能由调用者调整 |
| 缓存诊断与显式断点 | 未支持完整能力 | 仅发送 comparison_response_id，没有诊断结果模型或断点控制 |

公开 WebSocket 当前已支持 stream_id 多路复用，连接丢失后需恢复上下文；**当前串行是适配器限制，不是官方协议只允许串行**。共享缓存锚点不可替代事件路由。[WebSocket mode](https://developers.openai.com/api/docs/guides/websocket-mode)

官方对 steering 说明：被调整的响应可通过 incomplete（reason=steered）结束，再自动产生后继响应。当前统一抛错的逻辑不能直接兼容这一行为。[Incomplete event](https://developers.openai.com/api/reference/resources/responses/streaming-events#response-incomplete)

官方缓存指南对 GPT-5.6 及以后模型列出的 ttl 为 **"30m"**，更早模型使用相应 retention 配置。当前代码默认模型为 gpt-5.1，却统一发送上述 options，且使用 **"30ms"**；这不是已确认可移植的公共 API 请求。这里只确认文档与源码不一致，未确认 ChatGPT Codex 端点会拒绝、忽略或赋予其他含义；也不能从 key 稳定推导缓存必然命中。[Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)

## 6. API 家族边界

以下能力没有在本模块实现，不应混入模型 invoke/ainvoke 的承诺：

| 能力 | 当前范围 |
| --- | --- |
| HTTP Responses create / SSE | 只有 WebSocket 路径，没有 HTTP 回退 |
| Responses 查询、删除、input_items 列表 | 没有 REST 客户端或相关方法 |
| 后台生成、轮询、取消、流恢复 | 未实现；取消本地等待/关闭 socket 不等价于服务端 cancel |
| Conversations 持久对象 | 未实现；session_id 不是服务端 conversation ID |
| 自动/独立 compaction | 无 context_management 或 compact 请求；不保留 compaction 项供下次输入 |
| 文件上传、Webhook、输入 token 计数 | 未实现对应服务接口 |

后台模式包含独立的状态查询和取消能力；本地 asyncio 异步调用不代表服务端 background 模式。[Background mode](https://developers.openai.com/api/docs/guides/background)

Conversations 提供持久会话对象；它与基于 previous_response_id 的响应链是不同集成方式。[Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)

官方 compaction 包含 context_management 配置和独立 compact 端点。项目其他 middleware 的上下文裁剪，不属于对这些协议能力的实现。[Compaction](https://developers.openai.com/api/docs/guides/compaction)

## 7. 验证依据与改进顺序

本次重新运行 `python -m pytest tests/test_codex_oauth.py -q`：**42 passed、2 failed、13 subtests passed**。失败项为 test_build_request_includes_bound_tools_and_tool_choice、test_prompt_cache_key_is_stable_per_model_instance，均涉及 prompt_cache_key 旧期望。没有修改测试、源码或默认参数。

现有通过的测试覆盖文本/图片转换、函数和 custom 调用回放、加密 reasoning、增量历史、响应归并、串行连接复用和断线处理。它们使用本地替身，不证明认证、服务端 schema 或具体模型已经兼容。未执行真实模型调用或全套业务回归。

若后续扩展，建议优先处理会导致现有调用信息丢失的问题，再按业务需要添加能力：

1. **协议正确性：**明确目标端点与模型，处理 ttl 差异、无效选项静默丢失和无效锚点恢复；不要只凭参数名称透传全部字段。
2. **结果保真：**保存结构化错误、拒答、引用、phase 和必要输出条目；保留可判别失败原因。
3. **业务所需能力：**按实际需求增加原生结构化输出、文件输入或对外流式输出。
4. **独立扩展：**多路复用、steering、后台任务与原生内置工具需要配套状态和路由，不应仅增加请求字段后标为支持。

本文未更改架构图，也未将上述建议画成已存在的模块。
