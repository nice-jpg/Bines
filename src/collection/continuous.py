"""A serial collection loop above device operations and collection services."""

from dataclasses import dataclass, field
from enum import StrEnum
import json
import math
import hashlib
import logging
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from src.agent_runtime import tool_yield_reason
from src.tools.event_hub import EventHub, CollectionSwipe
from src.probe_state import MERCHANT_COLLECTION_DONE, COLLECTION_CONSECUTIVE_EMPTY_SWIPES
from .service import CollectionService, StepStatus


class StopStatus(StrEnum):
    COMPLETED = "completed"
    ATTENTION = "attention_required"
    YIELDED = "yielded"


@dataclass
class LoopProgress:
    update: dict[str, Any] = field(default_factory=dict)
    swipes: int = 0
    added: int = 0
    ratio: float | None = None
    hint: dict[str, Any] = field(default_factory=dict)


class ContinuousCollection:
    def __init__(self, service: CollectionService, event_hub: EventHub):
        self.service = service
        self.event_hub = event_hub

    def create_tool(self) -> StructuredTool:
        return StructuredTool.from_function(self.collect_continuously)

    def collect_continuously(self, x: int, y: int, change_threshold: float,
                             runtime: ToolRuntime[Any]) -> Command:
        """Collect the trained merchant with long swipes, short-swiping incomplete rows.

        Pass the exact collector change_threshold and coordinates inside the product pane.
        Returns on completion, anomalies, incoming user input or shutdown. No navigation.
        """
        state = dict(runtime.state)
        progress = LoopProgress()
        with self.event_hub.operation_lock:
            try:
                reason = self._validate(state, change_threshold)
                if x < 0 or not 150 <= y <= 2300:
                    reason = "invalid_swipe_coordinates"
                if reason:
                    return self._finish(runtime, progress, StopStatus.ATTENTION, reason)
                action = CollectionSwipe.LONG
                while True:
                    reason = tool_yield_reason(runtime)
                    if reason:
                        return self._finish(runtime, progress, StopStatus.YIELDED, reason)
                    completion = self.service.completion(state)
                    if completion.done:
                        progress.update.update(completion.update)
                        return self._finish(runtime, progress, StopStatus.COMPLETED, "collection_complete")
                    reason = self._validate(state, change_threshold)
                    if reason:
                        return self._finish(runtime, progress, StopStatus.ATTENTION, reason)
                    observation = self.event_hub.collection_swipe(action, x, y)
                    progress.swipes += 1
                    progress.update["page_collection_started"] = True
                    progress.hint = observation.hint
                    progress.ratio = (observation.change_ratio if observation.change_ratio is not None
                                      and math.isfinite(observation.change_ratio) else None)
                    if observation.error or observation.snapshot is None:
                        return self._finish(runtime, progress, StopStatus.ATTENTION,
                                            observation.error or "page_snapshot_missing")
                    if observation.change_ratio is None or not math.isfinite(observation.change_ratio):
                        return self._finish(runtime, progress, StopStatus.ATTENTION, "change_ratio_unavailable")
                    if observation.change_ratio > change_threshold:
                        return self._finish(runtime, progress, StopStatus.ATTENTION, "page_change_exceeded")
                    step = self.service.collect(state, observation.snapshot, action.value, {"x": x, "y": y})
                    state.update(step.update)
                    progress.update.update(step.update)
                    progress.added += step.new_count
                    if step.status == StepStatus.FAILED:
                        return self._finish(runtime, progress, StopStatus.ATTENTION, step.error)
                    action = CollectionSwipe.SHORT if step.status == StepStatus.INCOMPLETE else CollectionSwipe.LONG
            except Exception:
                # Never discard progress from earlier successful swipes on a late failure.
                logging.getLogger(__name__).exception("Continuous collection stopped unexpectedly")
                return self._finish(runtime, progress, StopStatus.ATTENTION, "collection_execution_failed")

    def _validate(self, state: dict[str, Any], threshold: float) -> str:
        completion = self.service.completion(state)
        if not completion.payload["ok"]:
            return completion.payload["error_type"]
        record = self.service.training_registry.record_for(state["page_visit_id"])
        if record is None or record.change_threshold is None:
            return "normal_swipe_training_required"
        if not str(record.merchant_profile.get("name") or "").strip():
            return "missing_merchant_profile"
        if not math.isfinite(threshold) or threshold != record.change_threshold:
            return "change_threshold_mismatch"
        script = self.service.mechanism.read_run_agent_py_snapshot(record.page_path)
        if not script["ok"] or hashlib.sha256(script["content"].encode()).hexdigest() != record.script_fingerprint:
            return "collection_script_requires_training"
        return ""

    @staticmethod
    def _finish(runtime: ToolRuntime[Any], progress: LoopProgress,
                status: StopStatus, reason: str) -> Command:
        if status == StopStatus.ATTENTION and progress.swipes:
            progress.update.update({COLLECTION_CONSECUTIVE_EMPTY_SWIPES: 0, MERCHANT_COLLECTION_DONE: False})
        payload = {"status": status.value, "reason": reason, "swipes": progress.swipes,
                   "appended_items": progress.added, "change_ratio": progress.ratio}
        if reason != "page_change_exceeded":
            payload["page_hint"] = progress.hint
        message = ToolMessage(content=json.dumps(payload, ensure_ascii=False),
                              tool_call_id=runtime.tool_call_id, name="collect_continuously")
        return Command(update={**progress.update, "messages": [message]})
