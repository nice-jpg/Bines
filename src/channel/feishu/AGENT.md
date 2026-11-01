# Feishu Channel

Module class responsibilities and sequence diagrams: [Channel Architecture](../Architecture.md).

FeishuChannel implements `src.channel.base.Channel`. It owns SDK lifecycle,
text normalization and vendor-message deduplication, but no Agent or checkpoint.

Before start, Host calls `bind_buffer(MessageBuffer)`. The adapter
converts SDK events into InBoundMessage with channel_id and session_id, then
submits them directly to that buffer on the application event loop. Foreign-loop
SDK callbacks are marshalled onto the owner loop. Message IDs are marked processed
only after acceptance. Channel IDs isolate deduplication across configured accounts.

Gateway groups input by channel/session. BatchCoordinator delivers batches to one
Root Agent; the adapter neither dispatches agents nor inspects their mailboxes.
Root replies through Gateway.send. `send_message(OutBoundMessage)` awaits the SDK
send with a 30-second timeout and returns its actual success flag. Session IDs are
chat IDs for both direct and group chat; outgoing messages target chat_id.

Application shutdown stops input first, waits for Agent workers, then closes
the adapter. SDK stop runs through asyncio.to_thread to avoid blocking the main
event loop. Third-party SDK internals are not part of the project thread model.

Configuration uses workspace/.env: FEISHU_APP_ID / FEISHU_APP_SECRET, alternatively
LARK_APP_ID / LARK_APP_SECRET or APP_ID / APP_SECRET. FEISHU_LOG_LEVEL defaults to INFO.

```bash
conda activate bines
python -m src.channel.feishu.run
python -m src.run_agent
```

Only text is accepted. SDK text batching, pre-admission deduplication and age-based
dropping are disabled; the project controls acceptance and grouping. Queues are
in memory and do not provide process-crash recovery or remote exactly-once sends.
The SDK owns global WebSocket lifecycle state; multiple Feishu accounts may require
process isolation. Gateway itself supports distinct channel IDs.

See [architecture](../../../docs/agent-system-design.md) and
[threading](../../../docs/threading-model.md) for current ownership.
