"""Cache-friendly conductor message projection and page-manual delivery."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.errors import GraphBubbleUp
from langgraph.runtime import Runtime

from src.conductor.progress import summarize_probe_progress
from src.conductor.state import ConductorState, current_visit
from src.page.mechanism import PageMechanism
from src.strategy.general.context.compression import DeviceContextCompressionMiddleware
from src.strategy.general.context.manual import PageManualMiddleware
from src.strategy.general.state import PageArrival, unconfirmed_arrival


class PageInputMiddleware(AgentMiddleware[ConductorState, None]):
    """Persist the active manual and project history for model requests."""

    state_schema: type[ConductorState] = ConductorState

    def __init__(self, *, mechanism: PageMechanism) -> None:
        self.mechanism: PageMechanism = mechanism
        self._manual: PageManualMiddleware = PageManualMiddleware(mechanism=mechanism)
        self._compression: DeviceContextCompressionMiddleware = DeviceContextCompressionMiddleware()

    def before_model(
        self, state: ConductorState, runtime: Runtime[None]
    ) -> dict[str, list[AnyMessage]] | None:
        messages: list[AnyMessage] = []
        pending: PageArrival | None = unconfirmed_arrival(state["general"])
        try:
            if pending is not None:
                content: str = self.mechanism.load_manual_bundle(pending["path"])
                candidate: SystemMessage = SystemMessage(
                    content=f"<candidate_page_manual>\nThis is an UNCONFIRMED destination. Old page manuals and merchant-phase instructions describe past visits, not the current device. Call confirm_page with your conclusion before further page operations; view queries and screenshots are optional. If mismatched, confirm with matches=false before corrective actions.\n{content.replace('current_page_manual', 'candidate_manual')}\n</candidate_page_manual>",
                    id=f"candidate-manual:{pending['candidate_id']}:{hashlib.sha256(content.encode()).hexdigest()[:12]}",
                )
                if not any(message.id == candidate.id for message in state["messages"]):
                    messages.append(candidate)
            else:
                manual: dict[str, list[AnyMessage]] | None = self._manual.before_model(state, runtime)
                if manual is not None:
                    messages.extend(manual["messages"])
        except GraphBubbleUp:
            raise
        except Exception:
            logging.getLogger(__name__).exception("Page manual unavailable; retaining autonomous operation")
            messages.append(
                SystemMessage(
                    content="Current page manual is unavailable. Do not treat an earlier page manual as current evidence; inspect the page before choosing an action."
                )
            )
        identity: str = current_visit(state)["visit_id"] or (pending["candidate_id"] if pending else "none")
        context: str = (
            f"Current confirmed page: {current_visit(state)['path']}. Page-local instructions only apply to this visit."
            if current_visit(state)["path"]
            else "No currently confirmed page. Old page and merchant instructions are historical. You may explore, correct arrival evidence or conclude the task."
        )
        self._append_context(state, messages, "page_identity", identity, context)
        if state["probe"].get("target_leaf_paths"):
            self._append_context(
                state,
                messages,
                "probe_progress",
                state["probe"].get("run_id", ""),
                "Task progress (facts only; not permission or completion requirements): "
                + json.dumps(summarize_probe_progress(state), ensure_ascii=False),
            )
        return {"messages": messages} if messages else None

    @staticmethod
    def _append_context(
        state: ConductorState, messages: list[AnyMessage], kind: str, identity: str, content: str
    ) -> None:
        digest: str = hashlib.sha256(content.encode()).hexdigest()[:12]
        message_id: str = f"{kind}:{identity}:{digest}"
        # Append changed facts; never rewrite a frozen user block or a cache prefix.
        latest: AnyMessage | None = next(
            (
                message
                for message in reversed(state["messages"])
                if message.id and message.id.startswith(f"{kind}:")
            ),
            None,
        )
        if latest is None or latest.id != message_id:
            messages.append(SystemMessage(content=content, id=message_id))

    async def abefore_model(
        self, state: ConductorState, runtime: Runtime[None]
    ) -> dict[str, list[AnyMessage]] | None:
        return self.before_model(state, runtime)

    def wrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[T]],
    ) -> ModelResponse[T]:
        return self._compression.wrap_model_call(request, handler)

    async def awrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[T]]],
    ) -> ModelResponse[T]:
        return await self._compression.awrap_model_call(request, handler)


__all__: list[str] = ["PageInputMiddleware"]
