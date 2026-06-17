from nice_dumper_agent.main_agent import request_optimizer_source
from nice_dumper_agent.models import FunctionRegion, RecognizerResult, ScoreResult


class FailingAgent:
    def invoke(self, state):
        raise RuntimeError("connection failed")


def test_request_optimizer_source_keeps_current_script_on_llm_failure() -> None:
    source = "def optimize(xml_text: str) -> str:\n    return xml_text\n"

    result = request_optimizer_source(
        main_agent=FailingAgent(),
        xml0="<hierarchy></hierarchy>",
        xml1="<hierarchy></hierarchy>",
        l0=RecognizerResult([FunctionRegion("[0,0][10,10]", "外卖")]),
        l1=RecognizerResult([FunctionRegion("[0,0][10,10]", "外卖")]),
        score=ScoreResult(80.0, 1.0, 0.0, 0, 0.0, []),
        optimizer_source=source,
    )

    assert result == source
