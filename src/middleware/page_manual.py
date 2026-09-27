"""Cache-friendly delivery of the current page manual to the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import BaseMessage, SystemMessage

try:
    from src.middleware.page_context import (
        PAGE_CONTEXT_ACTIVE,
        PAGE_CONTEXT_PATH,
        PAGE_VISIT_ID,
        PageContextState,
    )
    from src.page_mechanism import PageMechanism
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.page_context import (
        PAGE_CONTEXT_ACTIVE,
        PAGE_CONTEXT_PATH,
        PAGE_VISIT_ID,
        PageContextState,
    )
    from page_mechanism import PageMechanism


MANUAL_CONTEXT_KIND = "page_manual"


class PageManualMiddleware(AgentMiddleware[PageContextState]):
    """Persist page manuals at the history tail without changing the base prompt."""

    state_schema = PageContextState

    def __init__(self, *, mechanism: PageMechanism | None = None) -> None:
        self.mechanism = mechanism or PageMechanism()

    def before_model(self, state: PageContextState, runtime: Any) -> dict[str, Any] | None:
        return self._manual_state_update(state)

    async def abefore_model(self, state: PageContextState, runtime: Any) -> dict[str, Any] | None:
        return self._manual_state_update(state)

    def _manual_state_update(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        if not state.get(PAGE_CONTEXT_ACTIVE, False):
            return None
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        visit_id = str(state.get(PAGE_VISIT_ID) or "")
        if not current_path or not visit_id:
            return None

        bundle = self.mechanism.load_manual_bundle(current_path)
        digest = hashlib.sha256(bundle.encode("utf-8")).hexdigest()
        messages = state.get("messages") or []
        if _has_manual_message(messages, current_path, visit_id, digest):
            return None
        return {"messages": [_manual_message(current_path, visit_id, digest, bundle)]}


def _manual_message(current_path: str, visit_id: str, digest: str, bundle: str) -> SystemMessage:
    return SystemMessage(
        content=bundle,
        id=f"page-manual:{visit_id}:{digest[:12]}",
        additional_kwargs={
            "bines_context": {
                "kind": MANUAL_CONTEXT_KIND,
                "canonical_path": current_path,
                "page_visit_id": visit_id,
                "sha256": digest,
            }
        },
    )


def _has_manual_message(
    messages: Sequence[Any],
    current_path: str,
    visit_id: str,
    digest: str,
) -> bool:
    for message in reversed(messages):
        context = _message_context(message)
        if context.get("kind") != MANUAL_CONTEXT_KIND:
            continue
        return (
            context.get("canonical_path") == current_path
            and context.get("page_visit_id") == visit_id
            and context.get("sha256") == digest
        )
    return False


def _message_context(message: Any) -> Mapping[str, Any]:
    if isinstance(message, BaseMessage):
        additional_kwargs = message.additional_kwargs
    elif isinstance(message, Mapping):
        additional_kwargs = message.get("additional_kwargs")
    else:
        return {}
    if not isinstance(additional_kwargs, Mapping):
        return {}
    context = additional_kwargs.get("bines_context")
    return context if isinstance(context, Mapping) else {}


__all__ = ["PageManualMiddleware"]
