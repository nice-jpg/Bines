from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from page_strategy_helpers import page_fields

from src.conductor.state import read_conductor_state

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage

from src.agent_runtime.middleware.tool_error import ToolErrorMiddleware
from src.strategy.general.dangerous_tool import DangerousToolMiddleware
from src.tools.shared.factory import create_tool
from src.tools.shared.safety import (
    DANGEROUS_TOOL_POLICY_METADATA_KEY,
    DangerousActionJustification,
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
    metadata = {DANGEROUS_TOOL_POLICY_METADATA_KEY: policy} if policy is not None else {}
    args = {}
    if justification is not None:
        args["justification"] = justification
    return SimpleNamespace(
        tool_call={"id": "call-1", "name": tool_name, "args": args},
        tool=SimpleNamespace(name=tool_name, metadata=metadata),
        state=read_conductor_state(
            {
                **page_fields("meituan/外卖", "visit-1", "general"),
            }
        ),
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
        lambda request: handler_calls.append(request)
        or ToolMessage(
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

        def close_package(justification: DangerousActionJustification) -> str:
            """Close with validated reasons."""
            return "closed"

        call = _request(tool_name="close_package", policy=policy, justification=justification)
        call.tool = create_tool(close_package, metadata={DANGEROUS_TOOL_POLICY_METADATA_KEY: policy})
        result = ToolErrorMiddleware().wrap_tool_call(
            call,
            lambda call: middleware.wrap_tool_call(call, lambda request: handler_calls.append(request)),
        )

        assert result.status == "error"
        assert result.additional_kwargs["error_type"] == "ValidationError"
        assert handler_calls == []
        assert logger.records == []


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
