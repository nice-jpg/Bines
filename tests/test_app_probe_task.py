"""Probe startup is an explicit model decision, not input string matching."""

from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from engine_helpers import Model, ScriptedEngine, run_engine
from src.conductor.state import initial_fields
from src.prompts.initial_message import ProbeTaskSpec
from src.tools.probe import create_start_probe_tool


def test_config_builds_absolute_leaf_paths(tmp_path):
    from src.prompts import build_probe_task_spec

    (tmp_path / "config.xml").write_text(
        '<package name="com.sankuai.meituan"><city name="宿州">'
        '<args><address name="A"/><range size="1000"/></args>'
        '<subpage name="外卖"><leaf name="美食"/></subpage></city></package>',
        encoding="utf-8",
    )
    with patch("src.prompts.initial_message.WORKSPACE_ROOT", tmp_path):
        spec = build_probe_task_spec()
    assert spec.package_name == "com.sankuai.meituan"
    assert spec.target_leaf_paths == ("meituan/外卖/美食",)


def test_config_context_keeps_environment_and_collection_requirements(tmp_path):
    from src.prompts import build_initial_messages

    (tmp_path / "config.xml").write_text(
        '<package name="com.sankuai.meituan"><city name="宿州">'
        '<args><address name="A"/><range size="1000"/></args>'
        '<subpage name="美食"/></city></package>', encoding="utf-8",
    )
    with patch("src.prompts.initial_message.WORKSPACE_ROOT", tmp_path):
        environment, config = build_initial_messages()
    assert "<environment_context>" in environment
    assert "com.sankuai.meituan" in config
    assert "1000米" in config


def test_start_probe_initializes_state_and_returns_configuration():
    tool = create_start_probe_tool()
    with (
        patch("src.tools.probe.build_probe_task_spec", return_value=ProbeTaskSpec("app", ("app/food",))),
        patch("src.tools.probe.build_initial_messages", return_value=["environment", "configuration"]),
    ):
        result = tool.func(runtime=SimpleNamespace(state=initial_fields(), tool_call_id="start"))
    assert result.update["probe"]["run_id"]
    assert result.update["probe"]["package_name"] == "app"
    assert result.update["probe"]["target_leaf_paths"] == ["app/food"]
    message = result.update["messages"][0]
    assert message.tool_call_id == "start"
    assert message.content == "environment\n\nconfiguration"
    assert tool.tool_call_schema.model_json_schema()["properties"] == {}


def test_repeated_start_preserves_progress():
    state = initial_fields()
    state["probe"] = {"run_id": "existing", "completed_merchants": {"app/food": ["shop"]}}
    with patch("src.tools.probe.build_probe_task_spec") as config:
        result = create_start_probe_tool().func(runtime=SimpleNamespace(state=state, tool_call_id="repeat"))
    config.assert_not_called()
    assert set(result.update) == {"messages"}
    assert state["probe"]["completed_merchants"] == {"app/food": ["shop"]}


def test_model_can_start_probe_from_natural_language():
    model = Model(replies=[
        AIMessage(content="", tool_calls=[{"name": "start_app_probe", "args": {}, "id": "start"}]),
        AIMessage(content="configured"),
    ])
    engine = ScriptedEngine(model=model, tools=[create_start_probe_tool()])
    with (
        patch("src.tools.probe.build_probe_task_spec", return_value=ProbeTaskSpec("app", ("app/food",))),
        patch("src.tools.probe.build_initial_messages", return_value=["configured task"]),
    ):
        run_engine(engine, [{"role": "user", "content": "帮我看看附近的外卖商家并采集商品"}])
    assert engine._state["probe"]["run_id"]
    assert any(isinstance(m, ToolMessage) and m.content == "configured task" for m in model.seen[-1])
    engine.close()


def test_old_trigger_without_model_tool_call_does_not_start_probe():
    engine = ScriptedEngine(model=Model(replies=[AIMessage(content="discuss only")]))
    run_engine(engine, [{"role": "user", "content": "执行应用探测任务"}])
    assert engine._state["probe"] == {}
    engine.close()
