"""LangChain tools for the Bines agent."""

from .common import PageDocTool, create_common_tools
from .content_provider import create_content_provider_tools
from .event_hub import create_event_hub_tools
from .subagents import create_subagent_tools
try:
    from src.collector import CollectorManager, create_prepare_collector_tool
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism, PageRunHook
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from collector import CollectorManager, create_prepare_collector_tool
    from page_mechanism import CollectorTrainingRegistry, PageMechanism, PageRunHook


def collect_conductor_tools(
    *,
    model=None,
    mechanism: PageMechanism | None = None,
    page_run_hook: PageRunHook | None = None,
    event_hub=None,
    collector_manager: CollectorManager | None = None,
) -> list:
    """Collect tools exposed to the conductor agent."""

    mechanism = mechanism or PageMechanism()
    page_run_hook = page_run_hook or PageRunHook(mechanism)
    page_doc_tool = PageDocTool(mechanism=mechanism, page_run_hook=page_run_hook)
    if event_hub is None:
        from .event_hub import EventHub

        event_hub = EventHub()
    if collector_manager is None:
        collector_manager = CollectorManager(
            model=model,
            mechanism=mechanism,
        )
    return [
        *create_common_tools(page_doc_tool=page_doc_tool),
        *create_event_hub_tools(event_hub),
        *create_prepare_collector_tool(collector_manager),
    ]


def collect_tools(*, include_subagents: bool = False, subagent_manager=None, model=None) -> list:
    """Collect every LangChain tool managed by this package."""

    if not include_subagents:
        return collect_conductor_tools(model=model)

    tools = [
        *create_common_tools(include_legacy_manual=True),
        *create_event_hub_tools(),
        *create_content_provider_tools(),
    ]
    if include_subagents:
        if subagent_manager is None:
            raise ValueError("subagent_manager is required when include_subagents=True")
        tools.extend(create_subagent_tools(subagent_manager))
    return tools


__all__ = ["collect_conductor_tools", "collect_tools"]
