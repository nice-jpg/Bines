from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
import sys

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from middleware.page_context import (
    PAGE_CONTEXT_ACTIVE,
    PAGE_CONTEXT_PATH,
    PAGE_VISIT_ID,
)
from middleware.page_manual import PageManualMiddleware
from page_mechanism import PageMechanism


@dataclass
class FakeModelRequest:
    state: dict
    messages: list
    system_message: SystemMessage | None = None

    def override(self, **kwargs):
        return replace(self, **kwargs)


def _mechanism(tmp_path: Path) -> PageMechanism:
    root = tmp_path / "pages"
    for relative, page, task in (
        ("meituan", "home page", "home task"),
        ("meituan/外卖", "takeout page", "takeout task"),
    ):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "PAGE.md").write_text(page, encoding="utf-8")
        (directory / "task.md").write_text(task, encoding="utf-8")
    return PageMechanism(root)


def _state(*, path: str = "meituan/外卖", visit_id: str = "visit-1", messages=None, overlay=False) -> dict:
    return {
        PAGE_CONTEXT_ACTIVE: True,
        PAGE_CONTEXT_PATH: path,
        PAGE_VISIT_ID: visit_id,
        "messages": list(messages or []),
    }


def _manual_from(update: dict) -> SystemMessage:
    message = update["messages"][0]
    assert isinstance(message, SystemMessage)
    return message


def test_appends_page_then_task_as_persistent_tail_system_message(tmp_path: Path) -> None:
    middleware = PageManualMiddleware(mechanism=_mechanism(tmp_path))

    update = middleware.before_model(_state(messages=[HumanMessage(content="probe")]), runtime=None)

    assert update is not None
    manual = _manual_from(update)
    assert manual.content.index("takeout page") < manual.content.index("takeout task")
    assert '<current_page_manual canonical_path="meituan/外卖">' in manual.content
    assert "visit-1" not in manual.content
    assert manual.additional_kwargs["bines_context"]["page_visit_id"] == "visit-1"


def test_same_visit_path_and_digest_does_not_duplicate_manual(tmp_path: Path) -> None:
    middleware = PageManualMiddleware(mechanism=_mechanism(tmp_path))
    first = _manual_from(middleware.before_model(_state(), runtime=None))

    update = middleware.before_model(_state(messages=[first]), runtime=None)

    assert update is None


def test_new_visit_reappends_identical_manual_without_changing_content(tmp_path: Path) -> None:
    middleware = PageManualMiddleware(mechanism=_mechanism(tmp_path))
    first = _manual_from(middleware.before_model(_state(visit_id="visit-1"), runtime=None))

    second = _manual_from(
        middleware.before_model(_state(visit_id="visit-2", messages=[first]), runtime=None)
    )

    assert second.content == first.content
    assert second.id != first.id
    assert second.additional_kwargs["bines_context"]["page_visit_id"] == "visit-2"


def test_navigation_appends_new_manual_without_rewriting_history(tmp_path: Path) -> None:
    middleware = PageManualMiddleware(mechanism=_mechanism(tmp_path))
    first = _manual_from(middleware.before_model(_state(), runtime=None))
    history = [HumanMessage(content="probe"), first, HumanMessage(content="next")]

    update = middleware.before_model(
        _state(path="meituan", visit_id="visit-home", messages=history),
        runtime=None,
    )

    manual = _manual_from(update)
    assert "home page" in manual.content
    assert history[-1].content == "next"


def test_changed_manual_digest_appends_updated_content(tmp_path: Path) -> None:
    mechanism = _mechanism(tmp_path)
    middleware = PageManualMiddleware(mechanism=mechanism)
    first = _manual_from(middleware.before_model(_state(), runtime=None))
    (mechanism.root / "meituan" / "外卖" / "task.md").write_text("updated task", encoding="utf-8")

    second = _manual_from(middleware.before_model(_state(messages=[first]), runtime=None))

    assert "updated task" in second.content
    assert second.id != first.id


def test_missing_marker_after_summarization_restores_manual(tmp_path: Path) -> None:
    middleware = PageManualMiddleware(mechanism=_mechanism(tmp_path))
    summarized = [SystemMessage(content="Summary of prior conversation")]

    update = middleware.before_model(_state(messages=summarized), runtime=None)

    assert "takeout page" in _manual_from(update).content


def test_async_hooks_match_sync_behavior(tmp_path: Path) -> None:
    middleware = PageManualMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()

    update = asyncio.run(middleware.abefore_model(state, runtime=None))

    assert "takeout task" in _manual_from(update).content



class RecordingChatModel(BaseChatModel):
    seen_messages: list[list] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recording"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen_messages.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


def test_create_agent_persists_manual_and_model_sees_it_at_history_tail(tmp_path: Path) -> None:
    model = RecordingChatModel()
    middleware = PageManualMiddleware(mechanism=_mechanism(tmp_path))
    agent = create_agent(
        model=model,
        tools=[],
        system_prompt="stable base prompt",
        middleware=[middleware],
    )

    result = agent.invoke(_state(messages=[HumanMessage(content="probe")]))

    assert model.seen_messages[0][0].content == "stable base prompt"
    assert model.seen_messages[0][-1].type == "system"
    assert "takeout page" in model.seen_messages[0][-1].content
    persisted = [message for message in result["messages"] if message.id and message.id.startswith("page-manual:")]
    assert len(persisted) == 1


def test_chat_openai_payload_keeps_stable_prefix_and_manual_at_tail() -> None:
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(api_key="test", base_url="https://example.invalid", model="test")
    messages = [
        SystemMessage(content="stable base prompt"),
        HumanMessage(content="probe"),
        SystemMessage(content="<current_page_manual>manual</current_page_manual>"),
    ]

    payload = model._get_request_payload(messages)

    assert payload["messages"][0] == {"content": "stable base prompt", "role": "system"}
    assert payload["messages"][-1] == {
        "content": "<current_page_manual>manual</current_page_manual>",
        "role": "system",
    }
