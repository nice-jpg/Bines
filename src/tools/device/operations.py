"""Explicit model-facing device tool definitions."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from langchain_core.tools import StructuredTool

from src.device import DeviceError, DeviceOperationService
from src.json_types import JsonObject
from src.tool_factory import create_tool
from src.tools.device.pages import create_page_tools
from src.tools.device.screenshots import ImageContent, screenshot_content
from src.tools.operation_effect import (
    OPERATION_EFFECT_POLICY_METADATA_KEY,
    SCROLL_PAGE_CHANGE_POLICY,
    OperationEffectPolicy,
)
from src.tools.operation_policy import EXTERNAL_OPERATION_POLICY_METADATA_KEY, ExternalOperationPolicy
from src.tools.safety import (
    DANGEROUS_TOOL_POLICY_METADATA_KEY,
    DangerousActionJustification,
    DangerousToolPolicy,
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    operation_name: str
    returns_page: bool = False
    notice_reason: str | None = None
    effect_policy: OperationEffectPolicy | None = None


TOOL_SPECS: Final[
    tuple[ToolSpec, ToolSpec, ToolSpec, ToolSpec, ToolSpec, ToolSpec, ToolSpec, ToolSpec, ToolSpec]
] = (
    ToolSpec(
        "run_package",
        "Open an Android application by package name. This dangerous action requires a structured justification.",
        "run_package",
        notice_reason="Start the application probe task.",
    ),
    ToolSpec(
        "close_package",
        "Force-stop an Android application by package name. This dangerous action requires a structured justification.",
        "close_package",
        notice_reason="Complete the application probe task.",
    ),
    ToolSpec(
        "tap",
        "Tap (x, y). Provide x/y coordinates. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "tap",
        True,
    ),
    ToolSpec(
        "swipe_up",
        "Swipe up a short distance from (x, y) to load data below. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_up",
        True,
        effect_policy=SCROLL_PAGE_CHANGE_POLICY,
    ),
    ToolSpec(
        "swipe_up_long",
        "Swipe up a long distance from (x, y) to traverse a large collection region. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_up_long",
        True,
        effect_policy=SCROLL_PAGE_CHANGE_POLICY,
    ),
    ToolSpec(
        "swipe_to_random",
        "Swipe from (x, y) to a recorded random page state for collector training. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_to_random",
        True,
        effect_policy=SCROLL_PAGE_CHANGE_POLICY,
    ),
    ToolSpec(
        "swipe_down",
        "Swipe down a short distance from (x, y) to load data above or refresh. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_down",
        True,
        effect_policy=SCROLL_PAGE_CHANGE_POLICY,
    ),
    ToolSpec(
        "swipe_back",
        "Return to the last page. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_back",
        True,
    ),
    ToolSpec(
        "screenshot",
        "Capture the current screen and return a real image for visual target confirmation.",
        "screenshot",
    ),
)

COLLECTOR_ONLY_EVENT_HUB_TOOL_NAMES: Final[frozenset[str]] = frozenset({"swipe_to_random"})
EVENT_HUB_TOOL_NAMES: Final[tuple[str, ...]] = tuple(
    spec.name for spec in TOOL_SPECS if spec.name not in COLLECTOR_ONLY_EVENT_HUB_TOOL_NAMES
)
CONDUCTOR_EVENT_HUB_TOOL_NAMES: Final[tuple[str, str, str, str, str, str]] = (
    "run_package",
    "close_package",
    "swipe_up",
    "swipe_up_long",
    "swipe_down",
    "screenshot",
)


def create_event_hub_tools(
    event_hub: DeviceOperationService | None = None,
    *,
    tool_names: tuple[str, ...] | None = None,
) -> list[StructuredTool]:
    """Create one input-only LangChain tool per recorded operation."""

    hub: DeviceOperationService = event_hub or DeviceOperationService()
    selected_names: frozenset[str] = frozenset(tool_names or EVENT_HUB_TOOL_NAMES)
    tools: list[StructuredTool] = [
        _build_tool(spec, hub) for spec in TOOL_SPECS if spec.name in selected_names
    ]
    return tools + create_page_tools(hub.page_bridge) if tool_names is None else tools


def create_conductor_event_hub_tools(event_hub: DeviceOperationService | None = None) -> list[StructuredTool]:
    """Expose only non-ambiguous raw device operations to conductor."""

    return create_event_hub_tools(event_hub, tool_names=CONDUCTOR_EVENT_HUB_TOOL_NAMES)


def build_tools() -> list[StructuredTool]:
    """Return the default recorded touch-operation tools."""

    return create_event_hub_tools()


def _build_tool(spec: ToolSpec, hub: DeviceOperationService) -> StructuredTool:
    metadata: dict[str, ExternalOperationPolicy | DangerousToolPolicy | OperationEffectPolicy] = {}
    if spec.notice_reason is not None:
        metadata[EXTERNAL_OPERATION_POLICY_METADATA_KEY] = ExternalOperationPolicy(spec.notice_reason)
    if spec.operation_name in {"run_package", "close_package"}:
        metadata[DANGEROUS_TOOL_POLICY_METADATA_KEY] = DangerousToolPolicy(
            policy_id=f"src.device.operations.DeviceOperationService.{spec.operation_name}"
        )
    if spec.effect_policy is not None:
        metadata[OPERATION_EFFECT_POLICY_METADATA_KEY] = spec.effect_policy

    def register[**P, R](operation: Callable[P, R]) -> StructuredTool:
        return create_tool(
            func=operation,
            name=spec.name,
            description=spec.description,
            metadata=metadata,
            response_format="content_and_artifact" if spec.returns_page else "content",
        )

    operation_name: str = spec.operation_name
    if operation_name in {"run_package", "close_package"}:
        operation: Callable[[str], str | DeviceError] = (
            hub.run_package if operation_name == "run_package" else hub.close_package
        )

        def package_operation(
            package_name: str, *, justification: DangerousActionJustification
        ) -> str | JsonObject:
            DangerousActionJustification.model_validate(justification)
            result: str | DeviceError = operation(package_name)
            return result.as_dict() if isinstance(result, DeviceError) else result

        return register(package_operation)
    if operation_name == "tap":
        return register(hub.tap)
    if operation_name == "swipe_up":
        return register(hub.swipe_up)
    if operation_name == "swipe_up_long":
        return register(hub.swipe_up_long)
    if operation_name == "swipe_to_random":
        return register(hub.swipe_to_random)
    if operation_name == "swipe_down":
        return register(hub.swipe_down)
    if operation_name == "swipe_back":
        return register(hub.swipe_back)
    if operation_name == "screenshot":

        def screenshot() -> list[ImageContent]:
            return screenshot_content(hub)

        return register(screenshot)
    raise ValueError(f"Unsupported device operation: {operation_name}")
