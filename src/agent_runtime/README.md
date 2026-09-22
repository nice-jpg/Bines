# Agent Runtime

Framework execution infrastructure with no application-specific task semantics.

## Public API

- `create_runtime(name, model, tools, system_prompt, middleware=())` returns an `AgentRuntime` (keyword-only parameters).
- `AgentRuntime.run_turn(messages, session_id=...)` submits new input to a checkpointed graph.
- `AgentRuntime.resume_turn(session_id=..., user_input=...)` resumes the same thread.
- `AgentPersistenceLayer(runtime)` continues until an `AgentResponse` is accepted or human intervention is required.
- `AgentRunResult` carries output, graph state, interruption details and completion eligibility.

An accepted response has this JSON shape:

```json
{"resolved": true, "summary": "Task completed", "result": {}, "next_action": null}
```

An unresolved response must provide a nonempty `next_action`. Completion also
requires the runtime's state validation to succeed. Applications can specialize
protected input, completion and resume translation methods without changing the
execution loop. Native resume values are passed through unchanged by default.

## Internal Ownership

The runtime constructs tracing, model retry, tool-error handling and the
in-memory checkpointer. No trace object, retry callback or error handler is
accepted by the public factory. Business middleware remains caller-owned.

The required `name` parameter names both the agent graph and its trace.
Tracing reads only `NTRACE_ENABLED` and `NTRACE_SERVER_URL`; agent names do not
come from environment variables. The telemetry dependency is nTrace; execution depends on
LangChain, LangGraph and websockets, not application models or device modules.

The model retry policy allows five retries for recognized transient transport
errors. It does not inspect provider classes or provider-specific configuration.
Ordinary tool failures become error results rather than automatically replaying
side effects. Permanent execution failures preserve the checkpoint and pause.

Continuation messages use `agent_runtime_context` metadata and
`agent_persistence_continuation` markers. History is owned by the checkpointer:
new turns do not resubmit old messages. Checkpoints survive interruptions within
the process, not process restarts.
