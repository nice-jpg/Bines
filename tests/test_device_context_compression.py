from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


from src.strategy.general.context.compression import (
    MANUAL_PLACEHOLDER_CONTEXT_KIND,
    DeviceContextCompressionMiddleware,
    compact_device_messages,
)


def user(content: str) -> dict:
    return {"role": "user", "content": content}


def manual(path: str, visit: str, content: str | None = None) -> dict:
    return {
        "role": "system",
        "id": f"manual:{visit}",
        "content": content or f"manual for {path}",
        "additional_kwargs": {
            "bines_context": {
                "kind": "page_manual",
                "canonical_path": path,
                "page_visit_id": visit,
                "sha256": visit,
            }
        },
    }


def assistant_tool_calls(*calls: tuple[str, str, dict], content: str = "") -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": call_id, "name": name, "args": args}
            for call_id, name, args in calls
        ],
    }


def tool_result(call_id: str, name: str, content: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
    }


def call_pair(call_id: str, name: str, content: str | None = None, **args) -> list[dict]:
    return [
        assistant_tool_calls((call_id, name, args)),
        tool_result(call_id, name, content or f"result:{call_id}"),
    ]


def tool_call_ids(messages) -> list[str]:
    ids = []
    for message in messages:
        calls = message.get("tool_calls", []) if isinstance(message, dict) else (
            message.tool_calls if isinstance(message, AIMessage) else []
        )
        ids.extend(str(call.get("id") or "") for call in calls)
    return ids


def placeholder_paths(messages) -> list[str]:
    paths = []
    for message in messages:
        kwargs = message.additional_kwargs if isinstance(message, SystemMessage) else (
            message.get("additional_kwargs", {}) if isinstance(message, dict) else {}
        )
        context = kwargs.get("bines_context", {})
        if context.get("kind") == MANUAL_PLACEHOLDER_CONTEXT_KIND:
            paths.append(context["canonical_path"])
    return paths


def test_keeps_only_three_latest_short_lived_tool_pairs_in_active_manual() -> None:
    messages = [user("probe"), manual("meituan", "home")]
    for index, name in enumerate(("page_view", "tap_in_page", "swipe_up", "screenshot", "swipe_down")):
        messages.extend(call_pair(f"call-{index}", name))

    compacted = compact_device_messages(messages)

    assert tool_call_ids(compacted) == ["call-2", "call-3", "call-4"]
    result_ids = [message.get("tool_call_id") for message in compacted if isinstance(message, dict) and message.get("role") == "tool"]
    assert result_ids == ["call-2", "call-3", "call-4"]


def test_change_hints_remain_paired_and_allow_explicit_context_recovery() -> None:
    messages = [user("probe"), manual("meituan", "home")]
    for index in range(5):
        hint = json.dumps({"kind": "page_change_hint", "regions": [{"id": index, "update_ratio": 0.8}]})
        messages.extend(call_pair(f"swipe-{index}", "swipe_up", hint))
    full_view = json.dumps({"id": 4, "summary": "explicitly requested context", "regions": []})
    messages.extend(call_pair("query", "page_view", full_view, key=4))

    compacted = compact_device_messages(messages)

    assert tool_call_ids(compacted) == ["swipe-3", "swipe-4", "query"]
    results = [message for message in compacted if isinstance(message, dict) and message.get("role") == "tool"]
    assert results == [messages[-5], messages[-3], messages[-1]]
    assert json.loads(results[-1]["content"])["summary"] == "explicitly requested context"


def test_preserves_package_captcha_think_collector_and_unknown_tools() -> None:
    names = (
        "run_package",
        "authenticate_captcha",
        "captcha_authenticated",
        "think",
        "prepare_collector",
        "is_done",
        "custom_audit_tool",
        "close_package",
    )
    messages = [user("probe"), manual("meituan", "home")]
    for index, name in enumerate(names):
        messages.extend(call_pair(f"keep-{index}", name))

    compacted = compact_device_messages(messages)

    assert tool_call_ids(compacted) == [f"keep-{index}" for index in range(len(names))]
    assert all(f"result:keep-{index}" in str(compacted) for index in range(len(names)))


def test_navigate_to_collapses_parent_and_keeps_child_manual_full() -> None:
    messages = [
        user("probe"),
        manual("meituan", "home", "HOME MANUAL"),
        *call_pair("think-1", "think", "home reasoning"),
        *call_pair("nav-1", "navigate_to", "child xml", x=10, y=20, next_path="meituan/外卖"),
        manual("meituan/外卖", "takeout", "TAKEOUT MANUAL"),
        *call_pair("read-1", "page_view", "current xml"),
    ]

    compacted = compact_device_messages(messages)

    assert placeholder_paths(compacted) == ["meituan"]
    assert "HOME MANUAL" not in str(compacted)
    assert "home reasoning" not in str(compacted)
    assert "nav-1" not in str(compacted)
    assert "TAKEOUT MANUAL" in str(compacted)
    assert "read-1" in str(compacted)


def test_leaf_placeholder_uses_merchant_name_and_collected_item_total() -> None:
    messages = [
        user("probe"),
        manual("meituan/外卖/美食/商家", "merchant-1", "MERCHANT MANUAL"),
        *call_pair("back", "navigate_back", next_path="meituan/外卖/美食"),
        manual("meituan/外卖/美食", "list-2", "LIST MANUAL"),
    ]
    summaries = {
        "merchant-1": {
            "page_path": "meituan/外卖/美食/商家",
            "merchant_profile": {"name": "老王/炒面"},
            "collected_items": 17,
            "is_leaf": True,
        }
    }

    compacted = compact_device_messages(messages, collection_summaries=summaries)
    placeholder = next(
        message
        for message in compacted
        if isinstance(message, SystemMessage)
        and message.additional_kwargs.get("bines_context", {}).get("kind")
        == MANUAL_PLACEHOLDER_CONTEXT_KIND
    )

    assert placeholder.content == "At /meituan/外卖/美食/老王-炒面: requested return to meituan/外卖/美食; archived 17 items."
    assert placeholder.additional_kwargs["bines_context"]["canonical_path"] == "meituan/外卖/美食/商家"
    assert placeholder.additional_kwargs["bines_context"]["collected_items"] == 17


def test_placeholder_describes_recorded_intents_without_assuming_success() -> None:
    messages = [
        user("probe"), manual("app/list", "list"),
        *call_pair("view", "page_view"),
        *call_pair("close", "tap_in_page", purpose="dismiss_overlay"),
        *call_pair("train", "prepare_collector"),
        *call_pair("done", "is_done", '{"done": false}'),
        *call_pair("nav", "navigate_to", next_path="app/merchant", merchant_name="Noodle Shop"),
        manual("app/merchant", "merchant"),
    ]
    projected = compact_device_messages(messages)
    placeholder = next(message for message in projected if isinstance(message, SystemMessage))
    assert "queried the page view" in placeholder.content
    assert "attempted overlay dismissal" in placeholder.content
    assert "requested collection-script validation" in placeholder.content
    assert "checked collection completion" in placeholder.content
    assert "requested navigation to app/merchant for merchant Noodle Shop" in placeholder.content
    assert "archived" not in placeholder.content
    assert "0 items" not in placeholder.content


def test_non_leaf_placeholder_keeps_canonical_path() -> None:
    messages = [
        user("probe"),
        manual("meituan", "home", "HOME"),
        *call_pair("next", "navigate_to", next_path="meituan/外卖"),
        manual("meituan/外卖", "takeout", "TAKEOUT"),
    ]

    compacted = compact_device_messages(messages)
    placeholder = next(message for message in compacted if isinstance(message, SystemMessage))

    assert placeholder.content == "At /meituan: requested navigation to meituan/外卖."


def test_navigate_back_discards_child_and_restores_parent_frame() -> None:
    messages = [
        user("probe"),
        manual("meituan", "home-1", "HOME MANUAL OLD"),
        *call_pair("home-work", "think", "home work"),
        *call_pair("nav-child", "navigate_to", "child xml", next_path="meituan/外卖"),
        manual("meituan/外卖", "child-1", "CHILD MANUAL"),
        *call_pair("child-work", "prepare_collector", "collector ready"),
        *call_pair("nav-back", "navigate_back", "home xml", next_path="meituan"),
        manual("meituan", "home-2", "HOME MANUAL CURRENT"),
        *call_pair("home-current", "page_view", "current home xml"),
    ]

    compacted = compact_device_messages(messages)

    assert placeholder_paths(compacted) == ["meituan/外卖"]
    assert "CHILD MANUAL" not in str(compacted)
    assert "collector ready" not in str(compacted)
    assert "HOME MANUAL CURRENT" in str(compacted)
    assert "home work" in str(compacted)
    assert "nav-child" not in str(compacted)
    assert "nav-back" not in str(compacted)
    assert "home-current" in str(compacted)
    parent_manual_index = next(
        index
        for index, message in enumerate(compacted)
        if isinstance(message, dict) and message.get("content") == "HOME MANUAL CURRENT"
    )
    child_placeholder_index = next(
        index
        for index, message in enumerate(compacted)
        if isinstance(message, SystemMessage)
        and message.additional_kwargs.get("bines_context", {}).get("canonical_path")
        == "meituan/外卖"
    )
    old_parent_index = next(
        index for index, message in enumerate(compacted)
        if isinstance(message, dict) and message.get("content") == "HOME MANUAL OLD"
    )
    current_action_index = next(
        index for index, message in enumerate(compacted)
        if isinstance(message, dict) and any(call.get("id") == "home-current" for call in message.get("tool_calls", []))
    )
    assert old_parent_index < child_placeholder_index < parent_manual_index < current_action_index


def test_multilevel_page_stack_restores_declared_parent() -> None:
    messages = [
        user("probe"),
        manual("app", "a-1", "A OLD"),
        *call_pair("a-work", "think", "A WORK"),
        *call_pair("a-to-b", "navigate_to", next_path="app/b"),
        manual("app/b", "b-1", "B OLD"),
        *call_pair("b-work", "think", "B WORK"),
        *call_pair("b-to-c", "navigate_to", next_path="app/b/c"),
        manual("app/b/c", "c-1", "C MANUAL"),
        *call_pair("c-to-b", "navigate_back", next_path="app/b"),
        manual("app/b", "b-2", "B CURRENT"),
    ]

    compacted = compact_device_messages(messages)

    assert placeholder_paths(compacted) == ["app", "app/b/c"]
    assert "A OLD" not in str(compacted)
    assert "C MANUAL" not in str(compacted)
    assert "B CURRENT" in str(compacted)
    assert "B WORK" in str(compacted)
    assert "b-to-c" not in str(compacted)
    b_manual_index = next(
        index
        for index, message in enumerate(compacted)
        if isinstance(message, dict) and message.get("content") == "B CURRENT"
    )
    c_placeholder_index = next(
        index
        for index, message in enumerate(compacted)
        if isinstance(message, SystemMessage)
        and message.additional_kwargs.get("bines_context", {}).get("canonical_path")
        == "app/b/c"
    )
    assert c_placeholder_index < b_manual_index


def test_page_stack_survives_user_block_boundary() -> None:
    first_block = [
        user("first"),
        manual("app", "parent-1", "PARENT OLD"),
        *call_pair("parent-work", "think", "PARENT WORK"),
        *call_pair("to-child", "navigate_to", next_path="app/child"),
        manual("app/child", "child-1", "CHILD MANUAL"),
    ]
    messages = [
        *first_block,
        user("second"),
        *call_pair("to-parent", "navigate_back", next_path="app"),
        manual("app", "parent-2", "PARENT CURRENT"),
    ]

    compacted = compact_device_messages(messages)

    assert placeholder_paths(compacted) == ["app/child"]
    assert "PARENT CURRENT" in str(compacted)
    assert "PARENT WORK" in str(compacted)
    parent_index = next(
        index
        for index, message in enumerate(compacted)
        if isinstance(message, dict) and message.get("content") == "PARENT CURRENT"
    )
    child_index = next(
        index
        for index, message in enumerate(compacted)
        if isinstance(message, SystemMessage)
        and message.additional_kwargs.get("bines_context", {}).get("canonical_path")
        == "app/child"
    )
    second_user_index = next(
        index
        for index, message in enumerate(compacted)
        if isinstance(message, dict) and message.get("content") == "second"
    )
    assert second_user_index < child_index < parent_index


def test_new_user_block_reanchors_manual_without_changing_prior_block() -> None:
    first_block = [
        user("first request"),
        manual("meituan", "home", "HOME MANUAL"),
        *call_pair("think-1", "think", "first result"),
    ]
    first_projection = compact_device_messages(first_block)
    extended = [*first_block, user("second request"), *call_pair("read-1", "page_view", "xml")]

    second_projection = compact_device_messages(extended)

    assert second_projection[: len(first_projection)] == first_projection
    second_user_index = next(
        index for index, message in enumerate(second_projection) if isinstance(message, dict) and message.get("content") == "second request"
    )
    anchor = second_projection[second_user_index + 1]
    assert anchor["content"] == "HOME MANUAL"
    assert anchor["id"].startswith("page-manual-anchor:1:")


def test_expired_tool_call_removes_its_entire_ai_turn_and_all_results() -> None:
    calls = (
        ("old-read", "page_view", {}),
        ("keep-collector", "prepare_collector", {}),
        ("tap", "tap_in_page", {"x": 1, "y": 2}),
        ("up", "swipe_up", {"x": 1, "y": 2}),
        ("screen", "screenshot", {}),
    )
    messages = [
        user("probe"),
        manual("meituan", "home"),
        assistant_tool_calls(*calls, content="running tools"),
        *(tool_result(call_id, name, f"result:{call_id}") for call_id, name, _ in calls),
    ]

    compacted = compact_device_messages(messages)

    assert tool_call_ids(compacted) == []
    assert all(f"result:{call_id}" not in str(compacted) for call_id, _name, _args in calls)
    assert "running tools" not in str(compacted)


def test_filters_openai_raw_tool_calls_and_keeps_matching_results() -> None:
    messages = [user("probe"), manual("meituan", "home")]
    for index in range(4):
        call_id = f"raw-{index}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "additional_kwargs": {
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": "page_view",
                                    "arguments": "{}",
                                },
                            }
                        ]
                    },
                },
                tool_result(call_id, "page_view", f"xml-{index}"),
            ]
        )

    compacted = compact_device_messages(messages)
    raw_ids = [
        call["id"]
        for message in compacted
        if isinstance(message, dict)
        for call in message.get("additional_kwargs", {}).get("tool_calls", [])
    ]

    assert raw_ids == ["raw-1", "raw-2", "raw-3"]
    assert "xml-0" not in str(compacted)


def test_supports_langchain_message_objects() -> None:
    manual_message = SystemMessage(
        content="HOME MANUAL",
        id="manual:home",
        additional_kwargs={
            "bines_context": {
                "kind": "page_manual",
                "canonical_path": "meituan",
                "page_visit_id": "home",
            }
        },
    )
    messages = [HumanMessage(content="probe"), manual_message]
    for index in range(4):
        messages.extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"id": f"call-{index}", "name": "page_view", "args": {}}],
                ),
                ToolMessage(content=f"xml-{index}", tool_call_id=f"call-{index}", name="page_view"),
            ]
        )

    compacted = compact_device_messages(messages)

    assert isinstance(compacted[0], HumanMessage)
    assert isinstance(compacted[1], SystemMessage)
    assert tool_call_ids(compacted) == ["call-1", "call-2", "call-3"]
    assert not any(isinstance(message, ToolMessage) and message.tool_call_id == "call-0" for message in compacted)


def test_removes_langchain_ai_message_together_with_expired_tool_result() -> None:
    messages = [HumanMessage(content="probe"), SystemMessage(
        content="HOME MANUAL",
        additional_kwargs={
            "bines_context": {
                "kind": "page_manual",
                "canonical_path": "meituan",
                "page_visit_id": "home",
            }
        },
    )]
    for index in range(4):
        messages.extend(
            [
                AIMessage(
                    content=f"device operation {index}",
                    tool_calls=[{"id": f"call-{index}", "name": "page_view", "args": {}}],
                ),
                ToolMessage(content=f"xml-{index}", tool_call_id=f"call-{index}", name="page_view"),
            ]
        )

    compacted = compact_device_messages(messages)

    assert "device operation 0" not in str(compacted)
    assert "xml-0" not in str(compacted)
    assert "device operation 1" in str(compacted)


@dataclass
class FakeRequest:
    messages: list
    state: dict
    system_message: SystemMessage | None = None

    def override(self, **kwargs):
        return replace(self, **kwargs)


def test_middleware_projects_only_request_messages_and_does_not_mutate_state() -> None:
    middleware = DeviceContextCompressionMiddleware()
    messages = [user("probe"), manual("meituan", "home")]
    for index in range(4):
        messages.extend(call_pair(f"call-{index}", "page_view"))
    state = {"messages": list(messages)}
    system_prompt = SystemMessage(content="stable base prompt")
    request = FakeRequest(messages=list(messages), state=state, system_message=system_prompt)

    projected = middleware.wrap_model_call(request, lambda prepared: prepared)

    assert projected.system_message is system_prompt
    assert tool_call_ids(projected.messages) == ["call-1", "call-2", "call-3"]
    assert state["messages"] == messages
    assert request.messages == messages


def test_middleware_reads_collection_summaries_from_graph_state() -> None:
    middleware = DeviceContextCompressionMiddleware()
    messages = [
        user("probe"),
        manual("meituan/美食/商家", "merchant-1"),
        *call_pair("back", "navigate_back", next_path="meituan/美食"),
        manual("meituan/美食", "list-2"),
    ]
    state = {
        "messages": messages,
        "probe": {"visit_summaries": {
            "merchant-1": {
                "page_path": "meituan/美食/商家",
                "merchant_profile": {"name": "炒面"},
                "collected_items": 9,
                "is_leaf": True,
            }
        }},
    }

    projected = middleware.wrap_model_call(
        FakeRequest(messages=messages, state=state),
        lambda prepared: prepared,
    )

    assert "At /meituan/美食/炒面:" in str(projected.messages)
    assert "archived 9 items" in str(projected.messages)


def test_async_middleware_matches_sync_projection() -> None:
    middleware = DeviceContextCompressionMiddleware()
    messages = [user("probe"), manual("meituan", "home"), *call_pair("read", "page_view")]
    request = FakeRequest(messages=messages, state={"messages": messages})

    async def handler(prepared):
        return prepared

    projected = asyncio.run(middleware.awrap_model_call(request, handler))

    assert projected.messages == compact_device_messages(messages)


def test_documented_frame_with_system_messages() -> None:
    """Keep the documented projection tied to the actual message algorithm."""
    warning = SystemMessage(content="ARRIVAL", additional_kwargs={
        "bines_context": {"kind": "operation_warning", "source_tool_call_id": "nav"}
    })
    candidate = SystemMessage(content="CANDIDATE")
    advice = SystemMessage(content="ADMISSION", additional_kwargs={
        "bines_context": {"kind": "collection_admission", "source_tool_call_id": "s1"}
    })
    progress = SystemMessage(content="PROGRESS")
    messages = [
        user("probe"), manual("app/list", "list-1", "LIST"),
        *call_pair("p", "page_view"),
        *call_pair("nav", "navigate_to", next_path="app/list/merchant"),
        warning, candidate, *call_pair("confirm", "confirm_page"),
        manual("app/list/merchant", "shop-1", "SHOP"), progress,
        *call_pair("train", "prepare_collector"),
        *call_pair("s1", "swipe_up"), advice,
        *call_pair("s2", "swipe_up"),
        *call_pair("s3", "swipe_up"),
        *call_pair("s4", "swipe_up"),
    ]
    projected = compact_device_messages(messages)
    assert len(messages) == 23
    assert len(projected) == 13
    assert placeholder_paths(projected) == ["app/list"]
    assert tool_call_ids(projected) == ["train", "s2", "s3", "s4"]
    assert warning not in projected and candidate not in projected
    assert progress in projected and advice in projected
    assert projected[6] == advice  # Advice survives removal of its source s1 pair.
    assert messages[1]["content"] == "LIST"  # Projection leaves history intact.
