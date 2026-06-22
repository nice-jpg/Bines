# Feishu Channel

## Purpose

This package is a transport adapter for Feishu/Lark. It owns message receiving,
message sending, message de-duplication, and faithful event parsing. It does not own the
LangChain agent lifecycle.

The agent runtime in `src/run_agent.py` initializes this channel, receives
`IncomingMessage` objects, runs the agent, and sends final output back through the channel.
The agent runtime also binds `notify_user` to `FeishuChannelRuntime.build_notifier(...)`.

## Configuration

Configuration is loaded from `workspace/.env`.

Required credentials can use any one of these name pairs:

- `FEISHU_APP_ID` and `FEISHU_APP_SECRET`
- `LARK_APP_ID` and `LARK_APP_SECRET`
- `APP_ID` and `APP_SECRET`

Optional:

- `FEISHU_LOG_LEVEL`, default `INFO`

## Runtime

Channel-only smoke runner:

```bash
conda activate bines
python -m src.channel.feishu.run
```

Agent-owned runner:

```bash
conda activate bines
python -m src.run_agent
```

## Message Flow

1. `receiver.py` receives `im.message.receive_v1` events through Feishu long connection.
2. `messages.py` parses text messages while preserving IDs, timestamps, sender IDs,
   mentions, and raw event/message/sender objects.
3. `dedup.py` filters repeated `message_id` values before any agent callback is invoked.
4. `src/run_agent.py` receives `IncomingMessage`, injects message metadata into the agent
   context, and sends the final output through `FeishuChannelRuntime.send_text(...)`.
5. `notify_user` operation notices are sent through a notifier built by the channel.

## Initial Scope

- Supported: plain text receive and plain text send/reply.
- Preserved for future use: mentions, timestamps, sender IDs, and raw Feishu event data.
- Not supported yet: images, files, rich text, interactive cards, concurrent session queues,
  persistent chat memory, or HTTP callback mode.
- De-duplication is based on Feishu `message_id`, with in-memory LRU state and optional JSONL
  persistence under `workspace/logs/feishu_processed_messages.jsonl`.
