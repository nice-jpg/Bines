"""LangChain adapters for the public Reco page-query protocol."""

import json
from collections.abc import Callable

from langchain_core.tools import StructuredTool

from Reco.model_interface import TOOLS
from src.device import PageArtifact, PageBridge
from src.serialization.json import JsonValue


def create_page_tools(bridge: PageBridge) -> list[StructuredTool]:
    def bind(name: str) -> Callable[..., tuple[str, PageArtifact | None]]:
        def invoke(**kwargs: JsonValue) -> tuple[str, PageArtifact | None]:
            content, artifact = bridge.call(name, kwargs)
            return json.dumps(content, ensure_ascii=False), artifact

        return invoke

    return [
        StructuredTool(
            name=spec["name"],
            description=spec["description"],
            args_schema=spec["input_schema"],
            func=bind(spec["name"]),
            response_format="content_and_artifact",
        )
        for spec in TOOLS
    ]
