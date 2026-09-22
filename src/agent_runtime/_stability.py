"""Private ownership of tracing, retry policy and framework construction."""

from langgraph.checkpoint.memory import InMemorySaver
from nTrace import NTrace, createNTraceStartMiddleware, createNTraceEndMiddleware
from ._model_retry import create_model_retry_middleware
from ._tool_error import ToolErrorMiddleware


def build_graph(name, model, tools, system_prompt, middleware):
    from langchain.agents import create_agent

    trace = NTrace(agent_name=name, system_prompt=system_prompt)
    # NTrace reads NTRACE_ENABLED and NTRACE_SERVER_URL itself; delivery is fail-open.
    layers = [
        createNTraceStartMiddleware(trace),
        create_model_retry_middleware(),
        *middleware,
        ToolErrorMiddleware(),
        createNTraceEndMiddleware(trace),
    ]
    graph = create_agent(name=name, model=model, tools=list(tools), system_prompt=system_prompt,
                         middleware=layers, checkpointer=InMemorySaver())
    return graph, trace
