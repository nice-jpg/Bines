"""A serial collection loop above device operations and collection services."""

import hashlib
import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from src.agent_runtime.types.execution import YieldReason
from src.collector.training import CollectorTrainingRecord
from src.common.utils.json import JsonObject, JsonValue
from src.conductor.state import (
    ConductorFields,
    ConductorState,
    ConductorUpdate,
    collection_completion_update,
    combine_updates,
    complete_update,
    current_visit,
    merge_fields,
    read_conductor_fields,
)
from src.device import CollectionSwipe, DeviceOperationService, SwipeObservation
from src.page.script_types import ScriptSourceResult
from src.tools.shared.factory import create_tool

from .service import CollectionService, CollectionStepResult, CompletionResult, StepStatus


class StopStatus(StrEnum):
    COMPLETED = "completed"
    ATTENTION = "attention_required"
    YIELDED = "yielded"


@dataclass
class LoopProgress:
    update: ConductorUpdate = field(default_factory=lambda: {})
    swipes: int = 0
    added: int = 0
    ratio: float | None = None
    hint: JsonValue = field(default_factory=lambda: {})


class ContinuousCollection:
    def __init__(
        self,
        service: CollectionService,
        event_hub: DeviceOperationService,
        yield_reason: Callable[[ConductorState], YieldReason | None],
    ) -> None:
        self._yield_reason: Callable[[ConductorState], YieldReason | None] = yield_reason
        self.service: CollectionService = service
        self.event_hub: DeviceOperationService = event_hub

    def create_tool(self) -> StructuredTool:
        return create_tool(self.collect_continuously)

    def collect_continuously(
        self,
        x: int,
        y: int,
        change_threshold: float,
        runtime: ToolRuntime[None, ConductorState],
    ) -> Command[str]:
        """Collect the trained merchant with long swipes, short-swiping incomplete rows.

        Pass the exact collector view-update change_threshold and coordinates inside the product pane.
        Returns on completion, anomalies, incoming user input or shutdown. No navigation.
        """
        state: ConductorFields = read_conductor_fields(runtime.state)
        progress: LoopProgress = LoopProgress()
        with self.event_hub.operation_lock:
            try:
                reason: str | None = self._validate(state, change_threshold)
                if x < 0 or not 150 <= y <= 2300:
                    reason = "invalid_swipe_coordinates"
                if reason:
                    return self._finish(runtime, progress, StopStatus.ATTENTION, reason)
                action: CollectionSwipe = CollectionSwipe.LONG
                while True:
                    reason = self._yield_reason(runtime.state)
                    if reason:
                        return self._finish(runtime, progress, StopStatus.YIELDED, reason)
                    completion: CompletionResult = self.service.completion(state)
                    if completion.done:
                        progress.update = combine_updates(progress.update, completion.update)
                        return self._finish(runtime, progress, StopStatus.COMPLETED, "collection_complete")
                    reason = self._validate(state, change_threshold)
                    if reason:
                        return self._finish(runtime, progress, StopStatus.ATTENTION, reason)
                    observation: SwipeObservation = self.event_hub.collection_swipe(action, x, y)
                    progress.swipes += 1
                    progress.hint = observation.hint
                    progress.ratio = (
                        observation.change_ratio
                        if observation.change_ratio is not None and math.isfinite(observation.change_ratio)
                        else None
                    )
                    if observation.error or observation.snapshot is None:
                        return self._finish(
                            runtime,
                            progress,
                            StopStatus.ATTENTION,
                            observation.error or "page_snapshot_missing",
                        )
                    if observation.change_ratio is None or not math.isfinite(observation.change_ratio):
                        return self._finish(
                            runtime, progress, StopStatus.ATTENTION, "change_ratio_unavailable"
                        )
                    if observation.change_ratio > change_threshold:
                        return self._finish(runtime, progress, StopStatus.ATTENTION, "page_change_exceeded")
                    step: CollectionStepResult = self.service.collect(
                        state, observation.snapshot, action.value, {"x": x, "y": y}
                    )
                    state = merge_fields(state, step.update)
                    progress.update = combine_updates(progress.update, step.update)
                    progress.added += step.new_count
                    if step.status == StepStatus.FAILED:
                        return self._finish(runtime, progress, StopStatus.ATTENTION, step.error)
                    action = (
                        CollectionSwipe.SHORT
                        if step.status == StepStatus.INCOMPLETE
                        else CollectionSwipe.LONG
                    )
            except Exception:
                # Never discard progress from earlier successful swipes on a late failure.
                logging.getLogger(__name__).exception("Continuous collection stopped unexpectedly")
                return self._finish(runtime, progress, StopStatus.ATTENTION, "collection_execution_failed")

    def _validate(self, state: ConductorFields, threshold: float) -> str:
        completion: CompletionResult = self.service.completion(state)
        if not completion.payload["ok"]:
            return completion.payload.get("error_type", "collection_not_ready")
        record: CollectorTrainingRecord | None = self.service.training_registry.record_for(
            current_visit(state).get("visit_id", "")
        )
        if record is None or record.change_threshold is None:
            return "normal_swipe_training_required"
        name: JsonValue = record.merchant_profile.get("name")
        if not isinstance(name, str) or not name.strip():
            return "missing_merchant_profile"
        if not math.isfinite(threshold) or threshold != record.change_threshold:
            return "change_threshold_mismatch"
        script: ScriptSourceResult = self.service.mechanism.read_run_agent_py_snapshot(record.page_path)
        if (
            script["ok"] is False
            or hashlib.sha256(script["content"].encode()).hexdigest() != record.script_fingerprint
        ):
            return "collection_script_requires_training"
        return ""

    @staticmethod
    def _finish(
        runtime: ToolRuntime[None, ConductorState],
        progress: LoopProgress,
        status: StopStatus,
        reason: str,
    ) -> Command[str]:
        if status == StopStatus.ATTENTION and progress.swipes:
            progress.update = combine_updates(
                progress.update,
                {"merchant": {"evidence": {"consecutive_empty_swipes": 0}}},
                collection_completion_update(
                    merge_fields(read_conductor_fields(runtime.state), progress.update), False
                ),
            )
        payload: JsonObject = {
            "status": status.value,
            "reason": reason,
            "swipes": progress.swipes,
            "appended_items": progress.added,
            "change_ratio": progress.ratio,
        }
        if reason != "page_change_exceeded":
            payload["page_hint"] = progress.hint
        message: ToolMessage = ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=runtime.tool_call_id,
            name="collect_continuously",
        )
        return Command(
            update={
                **complete_update(read_conductor_fields(runtime.state), progress.update),
                "messages": [message],
            }
        )
