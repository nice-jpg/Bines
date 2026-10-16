"""Common framework result contract, independent of business middleware."""

from langchain_core.messages import ToolMessage
from langgraph.types import Command

type ToolResult = ToolMessage | Command[str]
