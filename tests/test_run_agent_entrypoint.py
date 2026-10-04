"""Entrypoint wiring; worker behavior is covered with real graphs separately."""

from unittest.mock import Mock

import pytest

from src import run_agent


@pytest.mark.parametrize("start_error", [None, RuntimeError("channel stopped")])
def test_main_wires_worker_and_closes_it_before_model(monkeypatch, start_error):
    events = []
    channel = Mock()
    channel.start.side_effect = start_error
    model = object()
    runtime = object()
    runner = Mock()
    runner.close.side_effect = lambda: events.append("worker closed")
    build_runtime = Mock(return_value=runtime)
    build_runner = Mock(return_value=runner)
    monkeypatch.setattr("sys.argv", ["run_agent", "--max-iterations", "42"])
    monkeypatch.setattr(run_agent, "load_feishu_config", lambda: "config")
    monkeypatch.setattr(run_agent, "FeishuChannelRuntime", lambda config: channel)
    monkeypatch.setattr(run_agent, "build_model", lambda: model)
    monkeypatch.setattr(run_agent, "build_conductor_runtime", build_runtime)
    monkeypatch.setattr(run_agent, "create_common_tools", lambda **kwargs: [])
    monkeypatch.setattr(run_agent, "FeishuAgentRunner", build_runner)
    monkeypatch.setattr(run_agent, "close_model", lambda value: events.append("model closed"))
    if start_error:
        with pytest.raises(RuntimeError, match="channel stopped"):
            run_agent.main()
    else:
        run_agent.main()
    build_runtime.assert_called_once()
    assert build_runtime.call_args.kwargs["model"] is model
    build_runner.assert_called_once_with(runtime, channel, 42)
    channel.start.assert_called_once_with(runner.on_message)
    assert events == ["worker closed", "model closed"]
