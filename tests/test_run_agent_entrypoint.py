from unittest.mock import Mock

import src.run_agent as entry


def test_entrypoint_constructs_root_before_application_start(monkeypatch):
    model = Mock()
    channel = Mock()
    seen = []

    class Host:
        def register_root(self):
            return Mock()

        async def run(self, root, channels):
            seen.append((root, channels))

    monkeypatch.setattr(entry.ConfigurationManager, "initialize", lambda: None)
    monkeypatch.setattr(entry, "trace_client", lambda: None)
    monkeypatch.setattr(entry, "load_feishu_config", lambda: None)
    monkeypatch.setattr(entry, "FeishuChannel", lambda *args: channel)
    monkeypatch.setattr(entry, "get_model_instance", lambda: model)
    close = Mock()
    monkeypatch.setattr(entry, "close_model", close)
    monkeypatch.setattr(entry, "Host", Host)
    root = Mock()
    monkeypatch.setattr(entry, "AssistantEngine", root)
    monkeypatch.setattr("sys.argv", ["run_agent", "--max-iterations", "42"])
    entry.main()
    assert len(seen) == 1 and seen[0][1] == [channel]
    assert seen[0][0] is root.return_value
    assert root.call_args.kwargs["model"] is model
    assert root.call_args.kwargs["max_iterations"] == 42
    close.assert_called_once_with(model)
