# Feishu Channel

`FeishuChannel` implements `src.channel.base.Channel`. It owns the Feishu SDK,
connection lifecycle, text parsing, deduplication and an outgoing buffer. It has
no reference to AgentRuntime, tools, checkpoints or agent conversation state.

The application creates one LongRunningAgent and one ChannelGateway. LongRunningAgent
accepts only `completion_driver: AgentCompletionDriver`, drives each task through
that driver, and keeps listening after completion. The driver and underlying
AgentRuntime do not add application worker threads. See the
[channel architecture](../../../docs/channel-architecture.md) for ownership and lifecycle.

Gateway owns
two private bounded queues; application code never receives queue references. Bind a message listener before `start()`. The listener returns whether
Gateway accepted the immutable IncomingMessage. Mark an event processed only
after acceptance. `send_message()` admits an OutgoingMessage to the channel
buffer; SDK delivery runs on the channel worker. `close()` drains accepted
outgoing work before disconnecting. SDK-owned network threads are additional
to the three application worker threads.

`talk`, operation notices, captcha prompts and final answers all enter the same
Gateway.send_message interface. LongRunningAgent receives input through
Gateway.receive_message. Neither tools nor LongRunningAgent operate Gateway queues
directly, and neither calls the SDK from an agent thread.

Gateway.request_stop closes input with Queue.shutdown(immediate=False). Accepted
input remains available; a drained closed input raises MessageStreamClosed.
LongRunningAgent submits its final output and calls Gateway.finish_output, which
closes output non-immediately. Gateway drains it before closing channels. No
control messages or sentinels travel in the business message queues.

Configuration uses `workspace/.env`: `FEISHU_APP_ID` / `FEISHU_APP_SECRET`, or
`LARK_APP_ID` / `LARK_APP_SECRET`, or `APP_ID` / `APP_SECRET`.
`FEISHU_LOG_LEVEL` defaults to `INFO`.

```bash
conda activate bines
python -m src.channel.feishu.run  # channel-only echo
python -m src.run_agent          # queue-connected agent
```

The adapter uses the public lifecycle API in installed lark-oapi 1.6.8.
Plain text only: direct chat output targets chat_id; group output replies to
message_id. Unsupported content stays inside the SDK boundary and is ignored.
SDK text batching, pre-admission deduplication and age-based dropping are disabled.
Workspace deduplication keeps the previous default Feishu message IDs; additional
channel IDs use a separate namespace. Queues and checkpoints are in memory;
process-crash recovery and exactly-once delivery are not provided.

The SDK's WebSocket client owns module-global event-loop state. Multiple Feishu
accounts in one process require a process-isolated Channel implementation; the
current entrypoint assembles one Feishu instance. Gateway itself supports multiple
Channel implementations with separate channel IDs and no extra agent queues.
