# Feishu Channel

## Purpose

This module connects the Bines LangChain agent to Feishu/Lark as a communication channel.
It receives plain text messages from Feishu, runs one agent turn, sends the final agent
output back to the same conversation, and forwards `notify_user` operation notices to the
same chat while the agent is working.

## Configuration

Configuration is loaded from `workspace/.env`, matching the rest of the project.

Required app credentials can use any one of these name pairs:

- `FEISHU_APP_ID` and `FEISHU_APP_SECRET`
- `LARK_APP_ID` and `LARK_APP_SECRET`
- `APP_ID` and `APP_SECRET`

Optional:

- `FEISHU_LOG_LEVEL`, default `INFO`

Model settings still use the existing model configuration from `workspace/.env`.

## Runtime

Start the long-connection receiver with:

```bash
conda activate bines
python -m src.channel.feishu.run
```

The implementation uses the official `lark_oapi` long-connection client, following the
Python echo bot sample under `src/channel/lark-samples-main`.

## Message Flow

1. `receiver.py` receives `im.message.receive_v1` events through Feishu long connection.
2. `messages.py` parses only plain text messages into `IncomingMessage`.
3. `agent_bridge.py` builds the initial agent context plus the Feishu text message.
4. A chat-bound `OperationNoticeTool` is injected before default tools are collected.
5. The agent runs through the existing LangChain `create_agent` harness.
6. The final agent output is sent back through `client.py`.

## Initial Scope

- Supported: plain text receive and plain text send/reply.
- Not supported yet: images, files, rich text, interactive cards, concurrent session queues,
  persistent chat memory, or HTTP callback mode.
- Communication failures in `notify_user` are logged but do not interrupt agent execution.
