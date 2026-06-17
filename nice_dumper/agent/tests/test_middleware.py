from nice_dumper_agent.middleware import RecognizerCommunicationMiddleware
from nice_dumper_agent.models import FunctionRegion, RecognizerResult


def test_recognizer_communication_middleware_records_call(monkeypatch) -> None:
    def fake_recognize_functions(model, xml_text):
        return RecognizerResult([FunctionRegion("[0,0][10,10]", "外卖")])

    monkeypatch.setattr("nice_dumper_agent.recognizer_agent.recognize_functions", fake_recognize_functions)
    middleware = RecognizerCommunicationMiddleware(model=object())

    result = middleware.call_recognizer("<hierarchy></hierarchy>")

    assert result.ok
    assert len(middleware.calls) == 1
    assert middleware.calls[0].function_count == 1
