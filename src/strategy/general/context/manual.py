"""Cache-friendly delivery of the current page manual to the model."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.runtime import Runtime

from src.agent_messages.types import MessageUpdate
from src.conductor.state import ConductorState, current_visit
from src.page.mechanism import PageMechanism
from src.strategy.general.context.messages import (
    MANUAL_CONTEXT_KIND,
    PAGE_MESSAGE_CONTEXT_KEY,
    PageMessageContext,
    read_page_message_context,
)


class PageManualMiddleware(AgentMiddleware[ConductorState, None]):
    """Persist page manuals at the history tail without changing the base prompt."""

    state_schema: type[ConductorState] = ConductorState

    def __init__(self, *, mechanism: PageMechanism | None = None) -> None:
        self.mechanism: PageMechanism = mechanism or PageMechanism()

    def before_model(
        self, state: ConductorState, runtime: Runtime[None]
    ) -> dict[str, list[AnyMessage]] | None:
        update: MessageUpdate | None = self._manual_state_update(state)
        return {"messages": update["messages"]} if update is not None else None

    async def abefore_model(
        self, state: ConductorState, runtime: Runtime[None]
    ) -> dict[str, list[AnyMessage]] | None:
        update: MessageUpdate | None = self._manual_state_update(state)
        return {"messages": update["messages"]} if update is not None else None

    def _manual_state_update(self, state: ConductorState) -> MessageUpdate | None:
        if not state["general"].get("active", False):
            return None
        current_path: str = str(current_visit(state).get("path") or "")
        visit_id: str = str(current_visit(state).get("visit_id") or "")
        if not current_path or not visit_id:
            return None

        bundle: str = self.mechanism.load_manual_bundle(current_path)
        digest: str = hashlib.sha256(bundle.encode("utf-8")).hexdigest()
        messages: list[AnyMessage] = state.get("messages", [])
        if _has_manual_message(messages, current_path, visit_id, digest):
            return None
        return {"messages": [_manual_message(current_path, visit_id, digest, bundle)]}


def _manual_message(current_path: str, visit_id: str, digest: str, bundle: str) -> SystemMessage:
    return SystemMessage(
        content=bundle,
        id=f"page-manual:{visit_id}:{digest[:12]}",
        additional_kwargs={
            PAGE_MESSAGE_CONTEXT_KEY: {
                "kind": MANUAL_CONTEXT_KIND,
                "canonical_path": current_path,
                "page_visit_id": visit_id,
                "sha256": digest,
            }
        },
    )


def _has_manual_message(
    messages: Sequence[AnyMessage],
    current_path: str,
    visit_id: str,
    digest: str,
) -> bool:
    for message in reversed(messages):
        context: PageMessageContext | None = read_page_message_context(message)
        if context is None or context.get("kind") != MANUAL_CONTEXT_KIND:
            continue
        return (
            context.get("canonical_path") == current_path
            and context.get("page_visit_id") == visit_id
            and context.get("sha256") == digest
        )
    return False


__all__: list[str] = ["PageManualMiddleware"]
