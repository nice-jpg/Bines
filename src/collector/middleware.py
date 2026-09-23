"""Keep visual evidence operations sequential without changing tool schemas."""

from langchain.agents.middleware import AgentMiddleware


class CollectorModelMiddleware(AgentMiddleware):
    def wrap_model_call(self, request, handler):
        return handler(request.override(model_settings={**request.model_settings, "parallel_tool_calls": False}))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(model_settings={**request.model_settings, "parallel_tool_calls": False}))
