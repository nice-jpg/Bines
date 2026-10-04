"""Private ownership of tracing, retry policy and framework construction."""

from langgraph.checkpoint.memory import InMemorySaver
from nTrace import NTrace, createNTraceHostMiddleware, createNTraceLLMMiddleware, createNTraceToolMiddleware
from ._model_retry import create_model_retry_middleware
from ._tool_error import ToolErrorMiddleware
from .communication import _Inbox


def build_graph(name, model, tools, system_prompt, middleware):
    from langchain.agents import create_agent

    trace = NTrace(agent_name=name, system_prompt=system_prompt)
    # NTrace reads NTRACE_ENABLED and NTRACE_SERVER_URL itself; delivery is fail-open.
    layers = [
        createNTraceHostMiddleware(trace),
        create_model_retry_middleware(),
        *middleware,
        ToolErrorMiddleware(),
        createNTraceToolMiddleware(trace),
        createNTraceLLMMiddleware(trace),
    ]
    graph = create_agent(name=name, model=model, tools=list(tools), system_prompt=system_prompt,
                         middleware=layers, checkpointer=InMemorySaver(), context_schema=_Inbox)
    return graph, trace
