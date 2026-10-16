"""Typed registration boundary for LangChain's callable schema inference."""

from collections.abc import Callable, Mapping
from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool

from src.serialization.json import JsonObject


def create_tool[**P, R, M](
    func: Callable[P, R],
    *,
    name: str | None = None,
    description: str | None = None,
    metadata: Mapping[str, M] | None = None,
    response_format: Literal["content", "content_and_artifact"] = "content",
) -> StructuredTool:
    """Preserve the original callable signature for tool schema construction."""
    return StructuredTool.from_function(  # pyright: ignore[reportUnknownMemberType]
        func=func,
        name=name,
        description=description,
        metadata=dict(metadata) if metadata is not None else None,
        response_format=response_format,
    )


def invoke_tool_json(tool: BaseTool, args: JsonObject) -> object:
    """Call the SDK's unparameterized dictionary input with validated JSON."""
    return tool.invoke(args)  # pyright: ignore[reportUnknownMemberType]
