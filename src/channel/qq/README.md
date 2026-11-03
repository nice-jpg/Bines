# QQ Channel

基于 [官方 botpy SDK](https://github.com/tencent-connect/botpy)，实现项目的 `Channel` 接口。
只处理非空文本；附件不下载。官方 sample 仅供参考，运行时使用安装的 `qq-botpy`。

在项目 Python 环境中安装：

```sh
python -m pip install -r src/channel/qq/requirements.txt
```

在 `workspace/config.toml` 的现有配置中加入以下内容（首次创建时还需顶层 `schema_version = 1`）：

```toml
[channel.qq]
default = "main"

[channel.qq.connections.main]
app_id = "你的 AppID"
app_secret = "你的 AppSecret"
sandbox = "false"
public_messages = "true"
public_guild_messages = "true"
direct_message = "true"
```

配置服务要求 channel 下所有值为字符串。三个消息开关分别订阅群/好友消息、
频道 @ 消息、频道私信；请按机器人已开通的权限启用，不使用的设为 `"false"`。
`load_qq_config("main")` 可显式选择连接；多个账号应设置不同的 `channel_id`。

从项目根目录运行独立回声验证：

```sh
python -m src.channel.qq.run
```

集成时创建 `QQChannel(load_qq_config(), "qq")`，交给现有 Host 的 channels 列表，
由 Host 绑定 buffer、启动和关闭；直接使用时需先 `bind_buffer` 再 `await start()`。
启动等待 READY，30 秒超时或鉴权失败会清理并抛错。实例关闭后不可重启。

会话地址分别是 `group:<group_openid>`、`c2c:<user_openid>`、
`guild:<channel_id>`、`dm:<guild_id>`；输出应原样使用入站的 channel_id/session_id。
SDK 在专用线程运行，输入始终回到应用事件循环提交，应用循环及异常处理器不受影响。

输出使用该会话最近一次成功入队消息的 ID 进行被动回复，群/好友回复序号递增。
由于公共 `OutBoundMessage` 没有原始消息 ID，不能精确指定回复会话内某条历史消息。
本地保守限制回复上下文为接收后 5 分钟；服务端仍可能因原消息过期、权限或配额拒绝。
未知会话、关闭状态、空文本、过期上下文返回 False；HTTP 错误与超时向调用者传播，
不自动重试或切换主动发送。返回 True 表示接口返回消息 ID，不代表用户已读。
去重和回复上下文各保留最多 4096 项，仅在当前进程有效。

离线测试不需要凭据；真实收发还需平台配置、相应权限及可访问 QQ API/WebSocket 的网络。
