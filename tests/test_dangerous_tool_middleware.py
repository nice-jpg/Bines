from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage

from middleware.dangerous_tool import DangerousToolMiddleware
from middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
from tools.safety import (
    DANGEROUS_TOOL_POLICY_METADATA_KEY,
    DangerousToolPolicy,
)


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def log(self, category: str, message: str, details: dict | None = None) -> None:
        self.records.append((category, message, details or {}))


def _justification(**overrides):
    value = {
        "reason": "Start the configured merchant probe.",
        "evidence": "The probe task is active and the application has not been launched.",
        "expected_outcome": "The application home page becomes available.",
        "risk_acknowledged": True,
    }
    value.update(overrides)
    return value


def _request(
    *,
    tool_name: str = "custom_device_action",
    policy: DangerousToolPolicy | None = None,
    justification=None,
):
    metadata = (
        {DANGEROUS_TOOL_POLICY_METADATA_KEY: policy}
        if policy is not None
        else {}
    )
    args = {}
    if justification is not None:
        args["justification"] = justification
    return SimpleNamespace(
        tool_call={"id": "call-1", "name": tool_name, "args": args},
        tool=SimpleNamespace(name=tool_name, metadata=metadata),
        state={
            PAGE_CONTEXT_PATH: "meituan/外卖",
            PAGE_VISIT_ID: "visit-1",
        },
    )


def _middleware() -> tuple[DangerousToolMiddleware, FakeLogger]:
    middleware = DangerousToolMiddleware()
    logger = FakeLogger()
    middleware.logger = logger
    return middleware, logger


def test_unmarked_tool_passes_through_without_audit() -> None:
    middleware, logger = _middleware()
    expected = ToolMessage(content="ok", tool_call_id="call-1", name="swipe_up")

    result = middleware.wrap_tool_call(
        _request(tool_name="swipe_up", justification={}),
        lambda _request: expected,
    )

    assert result is expected
    assert logger.records == []


def test_custom_policy_protects_tool_without_name_registration() -> None:
    middleware, logger = _middleware()
    policy = DangerousToolPolicy(policy_id="custom_policy")
    handler_calls = []

    result = middleware.wrap_tool_call(
        _request(policy=policy, justification=_justification()),
        lambda request: handler_calls.append(request) or ToolMessage(
            content="ok",
            tool_call_id="call-1",
            name="custom_device_action",
        ),
    )

    assert result.status == "success"
    assert len(handler_calls) == 1
    assert [record[2]["decision"] for record in logger.records] == ["approved", "executed"]
    assert logger.records[0][2]["policy"]["policy_id"] == "custom_policy"
    assert logger.records[0][2]["page_path"] == "meituan/外卖"
    assert logger.records[0][2]["page_visit_id"] == "visit-1"


def test_missing_or_invalid_justification_is_rejected_without_execution() -> None:
    policy = DangerousToolPolicy(policy_id="application_stop")
    invalid_values = [
        None,
        _justification(reason=" "),
        _justification(evidence=""),
        _justification(expected_outcome="\t"),
        _justification(risk_acknowledged=False),
    ]

    for justification in invalid_values:
        middleware, logger = _middleware()
        handler_calls = []
        result = middleware.wrap_tool_call(
            _request(tool_name="close_package", policy=policy, justification=justification),
            lambda request: handler_calls.append(request),
        )
        payload = json.loads(result.content)

        assert result.status == "error"
        assert payload["error_type"] == "dangerous_action_reason_invalid"
        assert payload["invalid_fields"]
        assert handler_calls == []
        assert logger.records[0][2]["decision"] == "rejected"


def test_valid_async_justification_executes_and_is_audited() -> None:
    middleware, logger = _middleware()
    policy = DangerousToolPolicy(policy_id="application_start")

    async def handler(_request):
        return ToolMessage(content="started", tool_call_id="call-1", name="run_package")

    result = asyncio.run(
        middleware.awrap_tool_call(
            _request(tool_name="run_package", policy=policy, justification=_justification()),
            handler,
        )
    )

    assert result.content == "started"
    assert [record[2]["decision"] for record in logger.records] == ["approved", "executed"]
    assert logger.records[0][2]["justification"] == _justification()


def test_execution_failure_is_logged_before_exception_propagates() -> None:
    middleware, logger = _middleware()
    policy = DangerousToolPolicy(policy_id="application_stop")

    def fail(_request):
        raise RuntimeError("device unavailable")

    try:
        middleware.wrap_tool_call(
            _request(tool_name="close_package", policy=policy, justification=_justification()),
            fail,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected execution failure to propagate to ToolErrorMiddleware.")

    assert [record[2]["decision"] for record in logger.records] == [
        "approved",
        "execution_failed",
    ]
    assert logger.records[-1][2]["result"]["error_type"] == "RuntimeError"
