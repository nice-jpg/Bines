from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from subagents_manager import FORK_BOILERPLATE_TAG, SubagentManager


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeAgent:
    def __init__(self, output_prefix: str = "done") -> None:
        self.output_prefix = output_prefix

    def invoke(self, state, config=None):
        messages = list(state["messages"])
        messages.append({"role": "assistant", "content": f"{self.output_prefix}:{len(state['messages'])}"})
        return {"messages": messages, "config": config or {}}


class SubagentManagerTests(unittest.TestCase):
    def make_manager(self, **kwargs) -> SubagentManager:
        return SubagentManager(
            model="model",
            parent_context_provider=kwargs.get("parent_context_provider", lambda: []),
            tool_factory=kwargs.get(
                "tool_factory",
                lambda: [
                    FakeTool("notify_user"),
                    FakeTool("authenticate_captcha"),
                    FakeTool("captcha_authenticated"),
                    FakeTool("think"),
                    FakeTool("query_manual"),
                    FakeTool("tap"),
                    FakeTool("uiautomate"),
                    FakeTool("append_excel_rows"),
                ],
            ),
            system_prompt="system",
            create_agent_factory=kwargs.get("create_agent_factory", lambda **_: FakeAgent()),
        )

    def test_spawn_creates_independent_and_delegated_subagents(self) -> None:
        manager = self.make_manager()

        independent = manager.spawn_subagent("solver", "independent", "Solve a standalone issue.")
        delegated = manager.spawn_subagent("merchant", "delegated", "Collect current merchant.")

        self.assertIn('id="subagent-1"', independent)
        self.assertIn('type="independent"', independent)
        self.assertIn('id="subagent-2"', delegated)
        self.assertIn('type="delegated"', delegated)

    def test_spawn_rejects_invalid_inputs_and_unknown_tools(self) -> None:
        manager = self.make_manager()

        self.assertIn('code="invalid_agent_type"', manager.spawn_subagent("x", "nested", "task"))
        self.assertIn('code="missing_instructions"', manager.spawn_subagent("x", "independent", " "))
        self.assertIn(
            'code="unknown_tool"',
            manager.spawn_subagent("x", "independent", "task", tool_names="missing_tool"),
        )
        self.assertIn('id="subagent-', manager.spawn_subagent("x", "independent", "task", tool_names="spawn_subagent"))

    def test_spawn_keeps_forbidden_subagent_tool_schemas_in_child_tool_set(self) -> None:
        manager = self.make_manager(
            tool_factory=lambda: [
                FakeTool("notify_user"),
                FakeTool("authenticate_captcha"),
                FakeTool("captcha_authenticated"),
                FakeTool("think"),
                FakeTool("query_manual"),
                FakeTool("tap"),
                FakeTool("spawn_subagent"),
                FakeTool("call_subagent"),
                FakeTool("kill_subagent"),
            ]
        )

        manager.spawn_subagent("solver", "independent", "Solve independently.")

        tool_names = [tool.name for tool in manager._records["subagent-1"].tools]
        self.assertEqual(
            tool_names,
            [
                "notify_user",
                "authenticate_captcha",
                "captcha_authenticated",
                "think",
                "query_manual",
                "tap",
                "spawn_subagent",
                "call_subagent",
                "kill_subagent",
            ],
        )
        spawn_tool = next(tool for tool in manager._records["subagent-1"].tools if tool.name == "spawn_subagent")
        self.assertIn("subagent_delegation_forbidden", spawn_tool.func("x", "delegated", "task"))

    def test_explicit_tool_names_auto_include_required_reasoning_tools(self) -> None:
        manager = self.make_manager()

        manager.spawn_subagent("merchant", "delegated", "Collect merchant.", tool_names="tap,uiautomate")

        tool_names = [tool.name for tool in manager._records["subagent-1"].tools]
        self.assertIn("notify_user", tool_names)
        self.assertIn("authenticate_captcha", tool_names)
        self.assertIn("captcha_authenticated", tool_names)
        self.assertIn("think", tool_names)
        self.assertIn("query_manual", tool_names)
        self.assertIn("tap", tool_names)
        self.assertIn("uiautomate", tool_names)

    def test_independent_subagent_preserves_its_own_history(self) -> None:
        manager = self.make_manager()
        manager.spawn_subagent("solver", "independent", "Solve independently.")

        first = manager.call_subagent("subagent-1", "first task")
        second = manager.call_subagent("subagent-1", "second task")

        self.assertIn("done:1", first)
        self.assertIn("done:3", second)
        self.assertEqual(len(manager._records["subagent-1"].messages), 4)

    def test_delegated_subagent_uses_parent_context_copy_without_writeback(self) -> None:
        parent_messages = [{"role": "user", "content": "parent context"}]
        seen_messages = []
        captured_create_agent_kwargs = []

        def create_agent_factory(**_):
            captured_create_agent_kwargs.append(_)

            class CapturingAgent:
                def invoke(self, state, config=None):
                    seen_messages.append(list(state["messages"]))
                    state["messages"].append({"role": "assistant", "content": "delegated done"})
                    return state

            return CapturingAgent()

        manager = self.make_manager(
            parent_context_provider=lambda: parent_messages,
            create_agent_factory=create_agent_factory,
        )
        manager.spawn_subagent("merchant", "delegated", "Collect merchant.")

        result = manager.call_subagent("subagent-1", "collect current merchant")

        self.assertIn("delegated done", result)
        self.assertEqual(parent_messages, [{"role": "user", "content": "parent context"}])
        self.assertEqual(manager._records["subagent-1"].messages, [])
        self.assertEqual(seen_messages[0][0]["content"], "parent context")
        self.assertIn(FORK_BOILERPLATE_TAG, seen_messages[0][-1]["content"])
        self.assertIn("Collect merchant.", seen_messages[0][-1]["content"])
        self.assertIn("collect current merchant", seen_messages[0][-1]["content"])
        self.assertEqual(captured_create_agent_kwargs[0]["system_prompt"], "system")

    def test_independent_subagent_keeps_rules_in_system_prompt(self) -> None:
        captured_create_agent_kwargs = []

        def create_agent_factory(**kwargs):
            captured_create_agent_kwargs.append(kwargs)
            return FakeAgent()

        manager = self.make_manager(create_agent_factory=create_agent_factory)
        manager.spawn_subagent("solver", "independent", "Solve independently.")
        manager.call_subagent("subagent-1", "first task")

        self.assertIn("Subagent rules:", captured_create_agent_kwargs[0]["system_prompt"])
        self.assertIn("Solve independently.", captured_create_agent_kwargs[0]["system_prompt"])

    def test_kill_subagent_removes_registry_entry(self) -> None:
        manager = self.make_manager()
        manager.spawn_subagent("solver", "independent", "Solve independently.")

        self.assertIn(">killed<", manager.kill_subagent("subagent-1"))
        self.assertIn('code="subagent_not_found"', manager.call_subagent("subagent-1", "task"))

    def test_call_subagent_returns_busy_error_when_another_call_is_active(self) -> None:
        manager = self.make_manager()
        manager.spawn_subagent("solver", "independent", "Solve independently.")

        manager._lock.acquire()
        try:
            result = manager.call_subagent("subagent-1", "task")
        finally:
            manager._lock.release()

        self.assertIn('code="subagent_busy"', result)


if __name__ == "__main__":
    unittest.main()
