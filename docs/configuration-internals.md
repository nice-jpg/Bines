# 配置子模块内部设计

状态：2026-10-14 已实现。全部类的职责、可见性、数据契约及公开接口以[配置架构第 4 节](configuration-architecture.md#4-完整类设计)为准。本文说明接口内部如何保证一次初始化、只读和并发访问；这些机制涉及的内部类及异常类只在类设计和本文说明，不在总体架构图中绘制。

## 一次读取与并发保证

管理器在主线程构建完整候选快照，完成校验、递归冻结后，持写锁一次性发布。未完成初始化的对象不会成为可查询单例，异常不会留下半份配置。

所有读取接口持共享读锁完成查找，并返回冻结对象或不可变视图。嵌套字典的原始可变对象不能保留在外部，单纯在返回类型上标注 `Mapping` 不足以保证只读。返回后无需继续持锁，调用者也不能通过嵌套容器修改快照。

`ReadWriteLock` 仅管理读者计数、写入占用和等待通知，允许多个读者并发，等待中的写入阻止新读者插队。临界区不做磁盘读取、网络调用或 SDK 初始化。初始化入口限制与原子发布共同保证只有一份完整配置；读锁履行共享访问契约，真正防止读取后被修改的是快照不可变性。

不使用普通互斥锁把所有读操作串行化后称其为“读者锁”。不为只读配置引入后台刷新或版本切换机制。

## 职责边界

模型组可配置 `affinity = "gpt-6.1-sol"`，值是 prompt 目录对应的模型名，不是 provider 分组或环境变量表达式。模型工厂将实际模型名与 affinity 写入实例 metadata；配置层不读取 prompt，也不维护 prompt 缓存。独立 loader 按每份角色文件依次选择模型目录、affinity 目录和 `gpt-6.1-sol`；页面机制按相同优先级选择完整目录，见 [Prompt 目录说明](../src/prompts/README.md)。

ConfigurationManager 提供配置查询，持有 ConfigurationSnapshot 和 ReadWriteLock。loader 负责读取、校验及递归冻结；Snapshot 只保存数据，锁只负责访问协调。调用者不能取得可变底层容器或内部锁，也不需要了解快照发布过程。

models.py 对外提供 get_model_instance(model_rule=None)，每次返回独立模型。coding/behave 通过 get_model_for_rule 选择对应组，规则缺省时回退 default；模型构造在退出配置读锁之后进行。该模块替代 application/model.py，不维护模型缓存。Root、conductor、collector 和 fork 构建处分别申请实例，Engine 负责其 Codex 连接释放。认证实现延迟导入，避免 Codex 查询配置时形成模块初始化循环。

`provider="deepseek"` 构造 `src.providers.deepseek.ChatDeepSeek`，其他 API-key 服务继续使用 `ChatOpenAI`。当前工厂固定采用非流式 Chat Completions；同步和异步响应共用 `_create_chat_result`，在模型返回前将 LangChain 的 `input_token_details.cache_read` 改为项目统计协议的 `cached_tokens`。如果响应只提供 DeepSeek 原生 `prompt_cache_hit_tokens`，适配器也会补入该字段。零命中保留为零，缺失用量不伪造计数；原始响应和其他用量字段不变。统计层不识别供应商，也不修改请求协议。

供应商实现统一放在 `src/providers/`：`codex/` 保存 OAuth、WebSocket 与协议类型，`deepseek.py` 保存用量适配。`configuration.models` 通过私有 `_MODEL_BUILDERS` 表选择构造函数：`codex` 使用专用构造函数，`deepseek`、`openai` 共享 API-key 构造过程并绑定各自模型类；未知 provider 沿用 OpenAI 兼容后端。配置选择与 prompt metadata 赋值统一在 `get_model_instance` 完成，不提供动态注册接口。Codex 延迟导入，避免其认证配置查询与配置包初始化循环；模型实例不缓存，关闭时仍释放实例独占的 Codex 连接。

## 实现对应

`src/configuration/manager.py` 使用类级共享锁保护单例发布；仅主线程可初始化，解析期间不持读写锁。解析失败不写入单例引用。所有业务查询取得读锁，但返回值递归不可变，因此返回后不需要继续持锁。

`loader.py` 用 MappingProxyType 封装新建映射、用 tuple 冻结序列，再构造 frozen dataclass。旧 dotenv 解析不写回进程环境；变量展开使用一次捕获的环境副本。`locking.py` 以 Condition 管理读者和写者，等待被异常中断及上下文退出均有 finally 清理。

单例生命周期测试使用独立 Python 子进程。业务集成测试注入独立冻结配置以隔离真实凭据和 Trace 网络请求，生产服务没有 reset 或 reload 接口。
