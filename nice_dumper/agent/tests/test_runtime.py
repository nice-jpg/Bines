from pathlib import Path
import json

from nice_dumper_agent.dumper import DumperConfig
from nice_dumper_agent.middleware import OptimizerTrace
from nice_dumper_agent.models import ScoreResult
from nice_dumper_agent.runtime import OptimizerRuntime


class FakeRecognizerAgent:
    def invoke(self, state):
        return {
            "messages": [
                {
                    "content": '{"functions":[{"bounds":"[0,0][10,10]","label":"外卖"}]}'
                }
            ]
        }


class RecordingRecognizerAgent(FakeRecognizerAgent):
    def __init__(self) -> None:
        self.last_state = None

    def invoke(self, state):
        self.last_state = state
        return super().invoke(state)


def test_runtime_spawn_call_kill_subagent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nice_dumper_agent.runtime.build_recognizer_agent", lambda model: FakeRecognizerAgent())
    optimizer = tmp_path / "workspace" / "optimize_xml.py"
    optimizer.parent.mkdir()
    optimizer.write_text("def optimize(xml_text: str) -> str:\n    return xml_text\n", encoding="utf-8")
    runtime = OptimizerRuntime(
        model=object(),
        dumper_config=DumperConfig(fixture_xml=None),
        output=optimizer,
        trace=OptimizerTrace(),
    )
    runtime.xml_store["XML0"] = '<hierarchy><node text="外卖" bounds="[0,0][10,10]" clickable="true"/></hierarchy>'

    spawned = runtime.spawn_subagent_tool("recognizer")
    assert "subagent-1" in spawned
    called = runtime.call_subagent_tool("subagent-1", "XML0")
    assert '"recognition_ref": "L0"' in called
    killed = runtime.kill_subagent_tool("subagent-1")
    assert '"killed": true' in killed


def test_runtime_filters_hidden_pull_layer_before_subagent_call(monkeypatch, tmp_path: Path) -> None:
    agent = RecordingRecognizerAgent()
    monkeypatch.setattr("nice_dumper_agent.runtime.build_recognizer_agent", lambda model: agent)
    runtime = _runtime(tmp_path, min_growth=1.0, stale_rounds=3)
    runtime.xml_store["XML0"] = """<hierarchy bounds="[0,0][100,100]">
      <node resource-id="container" bounds="[0,0][100,100]">
        <node resource-id="main" bounds="[0,0][100,100]">
          <node text="外卖" bounds="[0,0][50,50]" clickable="true" />
          <node text="搜索" bounds="[50,0][100,50]" clickable="true" />
        </node>
        <node resource-id="pull_loading_bg_container" bounds="[0,0][100,100]">
          <node content-desc="最近使用" bounds="[0,0][50,50]" clickable="false" />
        </node>
      </node>
    </hierarchy>"""

    runtime.spawn_subagent_tool("recognizer")
    runtime.call_subagent_tool("subagent-1", "XML0")

    prompt = agent.last_state["messages"][0]["content"]
    assert "外卖" in prompt
    assert "pull_loading_bg_container" not in prompt
    assert "最近使用" not in prompt


def test_runtime_score_round_returns_score_ref(tmp_path: Path) -> None:
    optimizer = tmp_path / "workspace" / "optimize_xml.py"
    optimizer.parent.mkdir()
    optimizer.write_text("def optimize(xml_text: str) -> str:\n    return xml_text\n", encoding="utf-8")
    runtime = OptimizerRuntime(
        model=object(),
        dumper_config=DumperConfig(fixture_xml=None),
        output=optimizer,
        trace=OptimizerTrace(),
    )
    runtime.xml_store["XML0"] = '<hierarchy><node text="外卖" bounds="[0,0][10,10]" clickable="true"/></hierarchy>'
    runtime.xml_store["XML1"] = runtime.xml_store["XML0"]
    runtime.call_subagent_tool = lambda subagent_id, xml_ref: ""
    from nice_dumper_agent.models import FunctionRegion, RecognizerResult

    runtime.recognition_store["L0"] = RecognizerResult([FunctionRegion("[0,0][10,10]", "外卖")])
    runtime.recognition_store["L1"] = RecognizerResult([FunctionRegion("[0,0][10,10]", "外卖")])

    scored = runtime.score_round_tool("XML0", "XML1", "L0", "L1")

    assert '"score_ref": "S1"' in scored


def test_optimizer_execution_error_is_supervised_and_scored_minus_1000(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, min_growth=1.0, stale_rounds=3)
    runtime.output.write_text(
        "def optimize(xml_text: str) -> str:\n"
        "    raise ValueError('cannot optimize malformed node')\n",
        encoding="utf-8",
    )
    from nice_dumper_agent.models import FunctionRegion, RecognizerResult

    runtime.recognition_store["L0"] = RecognizerResult(
        [FunctionRegion("[0,0][10,10]", "外卖")]
    )

    result = json.loads(runtime.optimize_xml_tool("XML0"))

    assert result["ok"] is False
    assert result["xml_ref"] == "XML1"
    assert result["score_ref"] == "S1"
    assert result["score"] == -1000.0
    assert result["stage"] == "execute"
    assert result["error_type"] == "ValueError"
    assert result["error"] == "cannot optimize malformed node"
    assert runtime.score_store["S1"].execution_error == (
        "ValueError: cannot optimize malformed node"
    )


def test_failed_optimizer_score_can_be_retrieved_without_recognizer_result(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, min_growth=1.0, stale_rounds=3)
    runtime.output.write_text(
        "def optimize(xml_text: str) -> str:\n"
        "    return 1 / 0\n",
        encoding="utf-8",
    )

    failed = json.loads(runtime.optimize_xml_tool("XML0"))
    scored = json.loads(runtime.score_round_tool("XML0", "XML1", "L0", "L1"))

    assert scored["score_ref"] == failed["score_ref"]
    assert scored["score"] == -1000.0
    assert scored["execution_error"] == "ZeroDivisionError: division by zero"


def test_optimizer_load_error_is_supervised(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, min_growth=1.0, stale_rounds=3)
    runtime.output.write_text("def optimize(:\n", encoding="utf-8")

    result = json.loads(runtime.optimize_xml_tool("XML0"))

    assert result["ok"] is False
    assert result["score"] == -1000.0
    assert result["stage"] == "load"
    assert "optimizer syntax error" in result["error"]


def test_negative_score_fluctuation_does_not_count_as_stale(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, min_growth=1.0, stale_rounds=1)
    runtime.score_store["S1"] = ScoreResult(100.0, 1.0, 0.0, 0, 0.0, [])
    runtime.score_store["S2"] = ScoreResult(80.0, 0.8, 0.0, 0, 0.0, [])

    runtime.apply_optimizer_tool("S1", "baseline", _script())
    result = runtime.apply_optimizer_tool("S2", "score fluctuated downward", _script())

    assert '"stale_count": 0' in result
    assert '"stop": false' in result


def test_slow_positive_growth_counts_as_stale(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, min_growth=1.0, stale_rounds=1)
    runtime.score_store["S1"] = ScoreResult(100.0, 1.0, 0.0, 0, 0.0, [])
    runtime.score_store["S2"] = ScoreResult(100.5, 1.0, 0.025, 0, 0.0, [])

    runtime.apply_optimizer_tool("S1", "baseline", _script())
    result = runtime.apply_optimizer_tool("S2", "small positive growth", _script())

    assert '"stale_count": 1' in result
    assert '"stop": true' in result


def _runtime(tmp_path: Path, *, min_growth: float, stale_rounds: int) -> OptimizerRuntime:
    optimizer = tmp_path / "workspace" / "optimize_xml.py"
    optimizer.parent.mkdir(exist_ok=True)
    optimizer.write_text(_script(), encoding="utf-8")
    runtime = OptimizerRuntime(
        model=object(),
        dumper_config=DumperConfig(fixture_xml=None),
        output=optimizer,
        trace=OptimizerTrace(),
        min_growth=min_growth,
        stale_rounds=stale_rounds,
    )
    runtime.xml_store["XML0"] = "<hierarchy></hierarchy>"
    runtime.xml_store["XML1"] = "<hierarchy></hierarchy>"
    runtime.xml_store["XML2"] = "<hierarchy></hierarchy>"
    return runtime


def _script() -> str:
    return "def optimize(xml_text: str) -> str:\n    return xml_text\n"
