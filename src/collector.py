"""Collector agent manager and page-collection tools."""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from langchain_core.tools import StructuredTool

try:
    from src.page_mechanism import PageMechanism, eval_collection
    from src.prompts import COLLECTOR_PROMPT
    from src.tools.event_hub import EventHub
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from page_mechanism import PageMechanism, eval_collection
    from prompts import COLLECTOR_PROMPT
    from tools.event_hub import EventHub


class CollectorManager:
    """Run an isolated collector agent for one page mechanism."""

    def __init__(
        self,
        *,
        model: Any,
        mechanism: PageMechanism | None = None,
        event_hub: EventHub | None = None,
        create_agent_factory: Callable[..., Any] | None = None,
        max_iterations: int = 80,
    ) -> None:
        self.model = model
        self.mechanism = mechanism or PageMechanism()
        self.event_hub = event_hub or EventHub()
        self.create_agent_factory = create_agent_factory
        self.max_iterations = max(1, int(max_iterations))

    def prepare_collector(
        self,
        page_path: str,
        page_description: str,
        required_fields: str,
        output_schema: str,
    ) -> str:
        """Delegate run.py preparation to an isolated collector."""

        agent = self._build_agent(page_path, required_fields, output_schema)
        message = "\n".join(
            [
                "<collector_assignment>",
                f"<page_path>{_escape_text(page_path)}</page_path>",
                f"<page_description>{_escape_text(page_description)}</page_description>",
                f"<required_fields>{_escape_text(required_fields)}</required_fields>",
                f"<output_schema>{_escape_text(output_schema)}</output_schema>",
                "<workflow>",
                "1. Call collector_uiautomate to inspect the real page XML.",
                "2. Scroll only if needed to find a suitable same-page collection region before baseline creation.",
                "3. Produce r1 directly from the current XML as the baseline JSON.",
                "4. Call save_baseline with r1. A later save_baseline replaces the stored baseline.",
                "5. Call sniff_run without changing page state to get r2.",
                "6. Call eval_collection with only r2; the tool uses the stored r1.",
                "7. If the tool score is below 100, analyze the mismatch cause, improve run.py with a general same-class-page heuristic, and repeat evaluation on the same XML/page state.",
                "</workflow>",
                "</collector_assignment>",
            ]
        )
        try:
            state = agent.invoke(
                {"messages": [{"role": "user", "content": message}]},
                config={"recursion_limit": self.max_iterations},
            )
        except Exception as exc:  # noqa: BLE001 - tool results should be structured.
            return json.dumps(
                {"ok": False, "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        return json.dumps(
            {
                "ok": True,
                "page_path": page_path,
                "result": _latest_text(state),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _build_agent(self, page_path: str, required_fields: str, output_schema: str) -> Any:
        create_agent = self.create_agent_factory
        if create_agent is None:
            from langchain.agents import create_agent as create_agent  # noqa: PLC0415

        return create_agent(
            model=self.model,
            tools=create_collector_tools(
                mechanism=self.mechanism,
                event_hub=self.event_hub,
                page_path=page_path,
                required_fields=required_fields,
                output_schema=output_schema,
            ),
            system_prompt=COLLECTOR_PROMPT,
            name="collector",
        )


class CollectorToolbox:
    """Page-scoped tool implementations exposed only to collector."""

    def __init__(
        self,
        *,
        mechanism: PageMechanism,
        event_hub: EventHub,
        page_path: str,
        required_fields: str = "",
        output_schema: str = "",
    ) -> None:
        self.mechanism = mechanism
        self.event_hub = event_hub
        self.page_path = page_path
        self.required_fields = required_fields
        self.output_schema = output_schema
        self._baseline: Any | None = None

    def collector_uiautomate(self) -> str:
        return str(self.event_hub.uiautomate())

    def collector_swipe_up(self, x: int, y: int) -> str:
        return str(self.event_hub.swipe_up(x, y))

    def collector_swipe_down(self, x: int, y: int) -> str:
        return str(self.event_hub.swipe_down(x, y))

    def read_run_py(self) -> str:
        return self.mechanism.read_run_py(self.page_path)

    def write_run_py(self, content: str) -> str:
        return self.mechanism.write_run_py(self.page_path, content)

    def sniff_run(self) -> str:
        xml = self.collector_uiautomate()
        return self.mechanism.sniff_run(self.page_path, xml)

    def save_baseline(self, r1: str) -> str:
        self._baseline = _coerce_json(r1)
        return json.dumps(
            {"ok": True, "baseline_saved": True, "baseline": self._baseline},
            ensure_ascii=False,
            sort_keys=True,
        )

    def eval_collection(self, r2: str) -> str:
        if self._baseline is None:
            return json.dumps(
                {
                    "score": 0,
                    "ok": False,
                    "issues": ["missing_baseline: call save_baseline before eval_collection"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        return json.dumps(
            eval_collection(self._baseline, r2, self.required_fields, self.output_schema),
            ensure_ascii=False,
            sort_keys=True,
        )


def create_collector_tools(
    *,
    mechanism: PageMechanism,
    event_hub: EventHub,
    page_path: str,
    required_fields: str = "",
    output_schema: str = "",
) -> list[StructuredTool]:
    toolbox = CollectorToolbox(
        mechanism=mechanism,
        event_hub=event_hub,
        page_path=page_path,
        required_fields=required_fields,
        output_schema=output_schema,
    )
    return [
        StructuredTool.from_function(
            func=toolbox.collector_uiautomate,
            name="collector_uiautomate",
            description="Read the current UIAutomator XML.",
        ),
        StructuredTool.from_function(
            func=toolbox.collector_swipe_up,
            name="collector_swipe_up",
            description="Scroll the current page upward. Collector may use this but cannot navigate into another page.",
        ),
        StructuredTool.from_function(
            func=toolbox.collector_swipe_down,
            name="collector_swipe_down",
            description="Scroll the current page downward. Collector may use this but cannot navigate back or click through.",
        ),
        StructuredTool.from_function(
            func=toolbox.read_run_py,
            name="read_run_py",
            description="Read the current page's run.py.",
        ),
        StructuredTool.from_function(
            func=toolbox.write_run_py,
            name="write_run_py",
            description="Replace the current page's run.py. Input: content.",
        ),
        StructuredTool.from_function(
            func=toolbox.sniff_run,
            name="sniff_run",
            description="Capture current XML and run the current page's run.py without changing page state.",
        ),
        StructuredTool.from_function(
            func=toolbox.save_baseline,
            name="save_baseline",
            description=(
                "Store the collector-produced baseline r1 JSON for later evaluation. "
                "Input: r1 JSON string. The baseline is unique; calling this again replaces the prior baseline."
            ),
        ),
        StructuredTool.from_function(
            func=toolbox.eval_collection,
            name="eval_collection",
            description=(
                "Compare stored baseline r1 with run.py result r2. Input: r2 JSON string only. "
                "The score and issues returned by this tool are the authoritative loop condition."
            ),
        ),
    ]


def create_prepare_collector_tool(manager: CollectorManager) -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            func=manager.prepare_collector,
            name="prepare_collector",
            description=(
                "Prepare the collector script for the current page before conductor operations. "
                "Inputs: page_path, page_description, required_fields, output_schema. "
                "Do not include task.md operation steps in page_description."
            ),
        )
    ]


def _latest_text(state: Any) -> str:
    messages: Sequence[Any] = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", [])
    if not messages:
        return ""
    latest = messages[-1]
    content = latest.get("content") if isinstance(latest, dict) else getattr(latest, "content", None)
    return content if isinstance(content, str) else str(content or "")


def _escape_text(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value
