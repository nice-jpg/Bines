"""Single-agent annotation, visual evidence, script repair and regression tools."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from threading import RLock
from typing import Literal, TypedDict
from uuid import uuid4

from langchain_core.tools import StructuredTool

from Reco.model_interface import TOOLS
from src.collector.evaluation import EvaluationGrade, eval_collection
from src.collector.types import NormalSwipeProfile, RestorationResult, TrainingResult, ValidationStamp
from src.collector.validation import CollectorValidationSession, ValidationCase
from src.common.utils.json import JsonObject, JsonValue, json_object, load_json
from src.device import (
    DeviceError,
    DeviceOperationService,
    PageBridge,
    PageOperationResult,
    PageSnapshot,
    ReadOnlyPageView,
)
from src.page.collection_contract import CollectionContract
from src.page.collection_types import CollectionGuide
from src.page.mechanism import PageMechanism
from src.page.script_types import PatchReport, ScriptResult, ScriptSourceResult, read_patch_report
from src.tools.device.screenshots import ImageContent, capture_png, image_url
from src.tools.shared.factory import create_tool

from .history import CollectorHistory


class TextContent(TypedDict):
    type: Literal["text"]
    text: str


def collection_items(value: JsonObject | None) -> list[JsonObject]:
    if value is None:
        return []
    items: JsonValue = value.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Collection items must be a list")
    return [json_object(item) for item in items]


@dataclass(frozen=True)
class NormalSwipeSample:
    operation_id: str
    action: Literal["swipe_up", "swipe_up_long"]
    revision: int
    ratio: float


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def object_value(value: object) -> JsonObject:
    try:
        return json_object(load_json(value) if isinstance(value, str) else value)
    except TypeError as exc:
        raise ValueError("Expected a JSON object") from exc


def shape_errors(schema: JsonValue, value: JsonValue, path: str = "result", *, partial: bool = False) -> list[str]:
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            return [path]
        if not schema:
            return []
        invalid_keys = set(value) - set(schema) if partial else set(value) ^ set(schema)
        errors: list[str] = [f"{path}.{key}" for key in invalid_keys]
        for key in set(value) & set(schema):
            errors.extend(shape_errors(schema[key], value[key], f"{path}.{key}", partial=partial))
        return errors
    if isinstance(schema, list):
        if not isinstance(value, list):
            return [path]
        return (
            [error for i, item in enumerate(value) for error in shape_errors(schema[0], item, f"{path}[{i}]", partial=partial)]
            if schema
            else []
        )
    if schema is not None and (type(schema) is not type(value)):
        if not (type(schema) in (int, float) and type(value) in (int, float)):
            return [path]
    return []


class CollectorToolbox:
    def __init__(
        self,
        *,
        mechanism: PageMechanism,
        event_hub: DeviceOperationService,
        page_path: str,
        run_id: str,
        merchant_schema: str = "{}",
        collection_schema: str | JsonObject = "{}",
        requires_merchant_profile: bool = False,
        history: CollectorHistory | None = None,
        page_visit_id: str | None = None,
    ) -> None:
        self.mechanism: PageMechanism = mechanism
        self.event_hub: DeviceOperationService = event_hub
        self.page_path: str = page_path
        self.page_visit_id: str = page_visit_id or uuid4().hex
        self.run_id: str = run_id
        self.merchant_schema: JsonObject = object_value(merchant_schema)
        self.collection_schema: JsonObject = object_value(collection_schema)
        self.requires_merchant_profile: bool = requires_merchant_profile
        self.history: CollectorHistory = history or CollectorHistory()
        cases: dict[str, ValidationCase] = {
            case["case_id"]: case for case in self.history.load(page_path, run_id=self.run_id)
        }
        self.validation: CollectorValidationSession = CollectorValidationSession(cases, set(cases))
        self._guide_version: str = ""
        self._profile: JsonObject = {}
        self._revision: int = 0
        self._swipe_samples: dict[str, NormalSwipeSample] = {}
        self._confirmed_swipes: tuple[str, ...] = ()
        self._random: bool = False
        self._saw_screenshot: bool = False
        self._profile_screenshot: bool = False
        self._read_regions: bool = False
        self._restored: bool = False
        self._tap_attempted: bool = False
        self._exposed_fingerprint: str | None = None
        self._repair_count: int = 0
        self._baseline_repair_count: int = 0
        self._lock: RLock = RLock()

    def _guide(self) -> CollectionGuide:
        return self.mechanism.read_collection_guide(self.page_path)

    def _script_fingerprint(self) -> str:
        return fingerprint(self.mechanism.read_run_agent_py(self.page_path))

    def _stamp(self) -> ValidationStamp:
        contract: CollectionContract | None = self.mechanism.collection_contract(self.page_path)
        return (
            self._script_fingerprint(),
            self._guide()["fingerprint"],
            fingerprint(encoded(self.validation.cases)),
            contract.fingerprint if contract else None,
        )

    def read_collection_contract(self) -> str:
        """Read existing PAGE.md identity and completeness rules; reuse them if evidence agrees."""
        contract: CollectionContract | None = self.mechanism.collection_contract(self.page_path)
        return encoded({"contract": contract.as_dict() if contract else None})

    def save_collection_contract(
        self, primary_key: list[str], required_fields: list[str], evidence: str = ""
    ) -> str:
        """Save missing rules or correct an evidenced counterexample; never relax rules for clipped rows."""
        contract: CollectionContract = CollectionContract.from_dict(
            {"primary_key": primary_key, "required_fields": required_fields}
        )
        item_schema: JsonValue = self.collection_schema.get("items")
        if not isinstance(item_schema, list) or not item_schema or not isinstance(item_schema[0], dict):
            raise ValueError("collection_schema must describe item fields")
        fields: set[str] = set(item_schema[0])
        if not set(contract.required_fields) <= fields:
            raise ValueError("Contract fields must exist in collection_schema.items")
        old: CollectionContract | None = self.mechanism.collection_contract(self.page_path)
        if old == contract:
            return encoded({"ok": True, "updated": False})
        current: list[ValidationCase] = [
            case for key, case in self.validation.cases.items() if key not in self.validation.historical_ids
        ]
        if len({case["revision"] for case in current if case["baseline"] is not None}) < 2:
            raise ValueError("Inspect and save multiple current states before defining rules")
        if old and not evidence.strip():
            raise ValueError("A concrete counterexample is required to change existing rules")
        self._validate_contract(contract)
        updated: bool = self.mechanism.save_collection_contract(self.page_path, contract)
        return encoded({"ok": True, "updated": updated, "fingerprint": contract.fingerprint})

    def _validate_contract(self, contract: CollectionContract) -> None:
        """Reject collisions within one visual state; clipped baseline rows are not rule counterexamples."""
        item_schema: JsonValue = self.collection_schema.get("items")
        if (
            not isinstance(item_schema, list)
            or not item_schema
            or not isinstance(item_schema[0], dict)
            or not set(contract.required_fields) <= set(item_schema[0])
        ):
            raise ValueError("Contract fields must exist in collection_schema.items")
        for case in self.validation.cases.values():
            seen: set[tuple[str, ...]] = set()
            for item in collection_items(case["baseline"]):
                if contract.missing(item):
                    continue
                key: tuple[str, ...] = contract.key(item)
                if key in seen:
                    raise ValueError(f"Nonunique visual key in case {case['case_id']}")
                seen.add(key)

    def read_collection_guide(self) -> str:
        """Read COLLECT.md and its required numeric aliases before annotating."""
        try:
            guide: CollectionGuide = self._guide()
        except FileNotFoundError:
            return encoded(
                {
                    "ok": False,
                    "error_type": "guide_missing",
                    "next": "Inspect regions and screenshot, then write_collection_guide",
                }
            )
        self._adopt_guide(guide)
        return encoded({"ok": True, **guide})

    def write_collection_guide(self, content: str) -> str:
        """Save a changed structural annotation guide and retire obsolete cases; identical content is a no-op.

        Write only when localization rules need correction. First line: JSON required_aliases,
        e.g. {"required_aliases":["1","2","3"]}; describe each anchor and its relationships in English.
        A changed guide requires fresh live samples, not re-marking old baselines to pass.
        """
        if not self._saw_screenshot or not self._read_regions:
            raise ValueError("Inspect region evidence and a screenshot before writing the guide")
        try:
            existing: CollectionGuide | None = self._guide()
        except FileNotFoundError:
            existing = None
        if existing is not None and existing["content"] == content:
            return encoded({"ok": True, "updated": False, "fingerprint": existing["fingerprint"]})
        guide: CollectionGuide = self.mechanism.write_collection_guide(self.page_path, content)
        invalidated: int = self._adopt_guide(guide)
        return encoded(
            {
                "ok": True,
                "updated": True,
                "fingerprint": self._guide_version,
                "invalidated_cases": invalidated,
                "next": "Mark the live page and collect fresh visual cases",
            }
        )

    def _adopt_guide(self, guide: CollectionGuide) -> int:
        """Bind validation to one annotation guide without erasing audit evidence."""
        self.history.retire_other_guides(self.page_path, guide["fingerprint"], run_id=self.run_id)
        obsolete: set[str] = {
            key
            for key, case in self.validation.cases.items()
            if case["guide_fingerprint"] != guide["fingerprint"]
        }
        if obsolete:
            for key in obsolete:
                del self.validation.cases[key]
            self.validation.historical_ids.difference_update(obsolete)
            self.validation.reviewed_images.difference_update(obsolete)
            self.validation.return_to_live()
            self.validation.invalidate()
            self._swipe_samples.clear()
            self._confirmed_swipes = ()
            self._restored = False
            self._tap_attempted = False
            self._random = False
        self._guide_version = guide["fingerprint"]
        self.validation.marked.clear()
        return len(obsolete)

    def read_run_agent_py(self, force: bool = False) -> str:
        """Read the current script; unchanged content is omitted unless force=true."""
        result: ScriptSourceResult = self.mechanism.read_run_agent_py_snapshot(self.page_path)
        if result["ok"] is False:
            return encoded(result)
        content: str = result["content"]
        digest: str = fingerprint(content)
        omitted: bool = not force and digest == self._exposed_fingerprint
        self._exposed_fingerprint = digest
        return encoded(
            {
                "ok": result["ok"],
                "canonical_path": result["canonical_path"],
                "path": result["path"],
                "script_fingerprint": digest,
                "content_omitted": omitted,
                "bytes": len(content.encode()),
                "lines": len(content.splitlines()),
                **({} if omitted else {"content": content}),
            }
        )

    def apply_run_agent_py_patch(self, patch: str) -> str:
        """Apply one scoped Codex Update File patch to run.agent.py only."""
        result: PatchReport = read_patch_report(
            self.mechanism.apply_run_agent_py_patch(self.page_path, patch)
        )
        if result.get("ok"):
            self._repair_count += 1
            self._exposed_fingerprint = result["script_fingerprint"]
        result["stagnation_review_required"] = self._repair_count >= 3
        return encoded(result)

    def _page_call(self, name: str, args: JsonObject) -> str:
        bridge: PageBridge = self.validation.frozen or self.event_hub.page_bridge
        guide: CollectionGuide | None = None
        if name == "update_aaid":
            guide = self._guide()
            if self._guide_version != guide["fingerprint"]:
                raise ValueError("Read the current guide before assigning aliases")
        if self.validation.active_id:
            result, _ = bridge.query(name, args)
        else:
            result, _ = bridge.call(name, args)
        if name in {"page_read", "page_node", "page_catalog"}:
            self._read_regions = True
        if name == "update_aaid" and guide is not None:
            alias: JsonValue = args["value"]
            if not isinstance(alias, str):
                raise ValueError("Alias must be a string")
            self.validation.marked.add(alias)
            if self.validation.active_id:
                case: ValidationCase = self.validation.cases[self.validation.active_id]
                case["snapshot"] = bridge.snapshot()
                if set(guide["required_aliases"]) <= self.validation.marked:
                    ReadOnlyPageView(case["snapshot"]).require_aliases(guide["required_aliases"])
                    case["guide_fingerprint"] = guide["fingerprint"]
        return encoded(result)

    def _move(
        self,
        action: Callable[[int, int], PageOperationResult],
        x: int,
        y: int,
        *,
        random_state: bool = False,
        normal_action: Literal["swipe_up", "swipe_up_long"] | None = None,
    ) -> str:
        if self.validation.active_id or self._restored:
            raise ValueError("Validation is frozen; save its baseline and return_to_live_page before moving")
        if self.requires_merchant_profile and not self._profile:
            raise ValueError("Save the merchant profile from the live top-state screenshot before moving")
        page, artifact = action(x, y)
        if artifact:
            self._revision += 1
            self._random = random_state
            self.validation.marked.clear()
            if normal_action:
                ratio: float | None = self.event_hub.page_bridge.root_change_ratio()
                if ratio is not None:
                    operation_id: str = uuid4().hex
                    self._swipe_samples[operation_id] = NormalSwipeSample(
                        operation_id, normal_action, self._revision, ratio
                    )
                    page = {**json_object(page), "operation_id": operation_id}
        return encoded(page)

    def collector_swipe_up(self, x: int, y: int) -> str:
        """Scroll outside frozen validation; returns page_change_hint IDs to query for content."""
        return self._move(self.event_hub.swipe_up, x, y, normal_action="swipe_up")

    def collector_swipe_up_long(self, x: int, y: int) -> str:
        """Scroll a long distance outside validation; returns page_change_hint IDs to query."""
        return self._move(self.event_hub.swipe_up_long, x, y, normal_action="swipe_up_long")

    def collector_swipe_down(self, x: int, y: int) -> str:
        """Scroll downward outside validation, never navigate; returns page_change_hint IDs to query."""
        return self._move(self.event_hub.swipe_down, x, y)

    def collector_swipe_to_random(self, x: int, y: int) -> str:
        """Visit a random validation state; returns page_change_hint IDs to query for content."""
        return self._move(self.event_hub.swipe_to_random, x, y, random_state=True)

    def capture_validation_case(self) -> str:
        """Freeze the annotated live view as a new visual case without running the script."""
        if self.validation.active_id is not None:
            raise ValueError("Return to the live page before capturing another case")
        guide: CollectionGuide = self._guide()
        if (
            self._guide_version != guide["fingerprint"]
            or not set(guide["required_aliases"]) <= self.validation.marked
        ):
            raise ValueError("Read the guide and explicitly mark every required alias on this state")
        snapshot: PageSnapshot = self.event_hub.page_bridge.snapshot()
        ReadOnlyPageView(snapshot).require_aliases(guide["required_aliases"])
        case_id: str = uuid4().hex
        self.validation.cases[case_id] = {
            "case_id": case_id,
            "page_visit_id": self.page_visit_id,
            "run_id": self.run_id,
            "snapshot": snapshot,
            "baseline": None,
            "image": None,
            "schema": self.collection_schema,
            "revision": self._revision,
            "random_state": self._random,
            "guide_fingerprint": guide["fingerprint"],
        }
        self._activate(case_id)
        return encoded(
            {"ok": True, "case_id": self.validation.active_id, "awaiting": "screenshot_then_save_baseline"}
        )

    def sniff_run(self) -> str:
        """Test all prepared cases; return summary counts and only failed cases' expected/actual results."""
        if not self.validation.cases:
            raise ValueError("Prepare visual cases before running regression")
        if any(case["baseline"] is None or not case.get("image") for case in self.validation.cases.values()):
            raise ValueError("Finish visual baseline preparation before running regression")
        return self._evaluate()

    def screenshot(self) -> list[ImageContent | TextContent]:
        """Return actual PNG image evidence for the current live or frozen historical state."""
        case: ValidationCase | None = (
            self.validation.cases.get(self.validation.active_id)
            if self.validation.active_id is not None
            else None
        )
        saved_image: str | None = case["image"] if case is not None else None
        if saved_image is not None and saved_image != "":
            image: str = saved_image
        else:
            image = image_url(capture_png(self.event_hub))
            if self._revision == 0:
                self._profile_screenshot = True
            if case:
                case["image"] = image
                case["screenshot_at"] = datetime.now(UTC).isoformat()
        self._saw_screenshot = True
        if case is not None and self.validation.active_id is not None:
            self.validation.reviewed_images.add(self.validation.active_id)
        return [
            {
                "type": "text",
                "text": encoded(
                    {
                        "case_id": self.validation.active_id,
                        "schema": self.collection_schema,
                        "instruction": "Extract only visually confirmed information; do not infer from script output.",
                    }
                ),
            },
            {"type": "image_url", "image_url": {"url": image}},
        ]

    def save_baseline(self, r1: str) -> str:
        """Save the independent screenshot baseline once; historical baselines are immutable."""
        if self.validation.active_id is None or self.validation.active_id in self.validation.historical_ids:
            raise ValueError("A new live validation case is required")
        case: ValidationCase = self.validation.cases[self.validation.active_id]
        if not case["image"] or case["baseline"] is not None:
            raise ValueError("Capture screenshot first; an accepted baseline cannot be overwritten")
        baseline: JsonObject = object_value(r1)
        errors: list[str] = shape_errors(self.collection_schema, baseline, partial=True)
        if errors:
            raise ValueError(f"Baseline does not match collection_schema: {errors}")
        case["baseline"] = baseline
        self.validation.invalidate()
        self._save_cases()
        return encoded({"ok": True, "case_id": self.validation.active_id, "saved": True})

    def repair_baseline(self, r1: str, evidence: str) -> str:
        """Correct a saved baseline from its original screenshot, retaining every prior revision."""
        case: ValidationCase | None = (
            self.validation.cases.get(self.validation.active_id)
            if self.validation.active_id is not None
            else None
        )
        if (
            not case
            or case["baseline"] is None
            or self.validation.active_id not in self.validation.reviewed_images
        ):
            raise ValueError("Load the saved case and inspect its original screenshot before repair")
        if not evidence.strip():
            raise ValueError("Describe the erroneous fields and what the original screenshot shows")
        baseline: JsonObject = object_value(r1)
        errors: list[str] = shape_errors(case["schema"], baseline, partial=True)
        if errors:
            raise ValueError(f"Baseline does not match the saved schema: {errors}")
        if baseline == case["baseline"]:
            return encoded({"ok": True, "changed": False})
        previous: ValidationCase = case.copy()
        case["baseline_revisions"] = [
            *case.get("baseline_revisions", []),
            {
                "baseline": case["baseline"],
                "evidence": evidence.strip(),
                "revised_at": datetime.now(UTC).isoformat(),
            },
        ]
        case["baseline"] = baseline
        self.validation.invalidate()
        try:
            self._save_cases()
        except Exception:
            self.validation.cases[self.validation.active_id] = previous
            raise
        self.validation.reviewed_images.discard(self.validation.active_id)
        self._baseline_repair_count += 1
        response: JsonObject = {
            "ok": True, "changed": True, "regression_required": True,
            "baseline_repair_count": self._baseline_repair_count,
            "baseline_review_required": self._baseline_repair_count >= 2,
        }
        if self._baseline_repair_count >= 2:
            response["warning"] = (
                "Repeated baseline repairs in this training visit. Use talk to report the evidence "
                "problem and an improvement plan. Review screenshot/view alignment, clipping, anchor "
                "selection and sampling before further repairs. Extra script data and fuller text "
                "are already allowed; never alter visual evidence merely to pass regression."
            )
        return encoded(response)

    def _evaluate(self) -> str:
        guide: CollectionGuide = self._guide()
        contract: CollectionContract | None = self.mechanism.collection_contract(self.page_path)
        if contract is None:
            raise ValueError("Define the collection contract from visual evidence before regression")
        self._validate_contract(contract)
        failures: list[JsonObject] = []
        grades: dict[str, EvaluationGrade] = {}
        for case_id, case in self.validation.cases.items():
            result: ScriptResult | None = None
            grade: EvaluationGrade
            if case["guide_fingerprint"] != guide["fingerprint"]:
                grade = {"score": 0, "errors": ["remark_required"], "missing": []}
            elif case["schema"] != self.collection_schema:
                grade = {"score": 0, "errors": ["history_schema_mismatch"], "missing": []}
            elif case["baseline"] is None or not case.get("image"):
                grade = {"score": 0, "errors": ["visual_baseline_required"], "missing": []}
            else:
                result = self.mechanism.run_page_script(self.page_path, ReadOnlyPageView(case["snapshot"]))
                grade = eval_collection(case["baseline"], result)
                if result["ok"] is False:
                    grade["execution_error"] = result
                elif shape_errors(self.collection_schema, result["result"]):
                    grade = {"score": 0, "errors": ["output_schema_mismatch"], "missing": []}
            if grade["score"] != 100:
                failures.append({
                    "case_id": case_id,
                    "ground_truth": case["baseline"],
                    "run_result": (
                        result["result"] if result is not None and result["ok"] is True
                        else result if result is not None
                        else {"ok": False, "errors": grade["errors"]}
                    ),
                })
            grades[case_id] = grade
        self.validation.grades = grades
        self.validation.tested_stamp = self._stamp()
        return encoded(
            {
                "summary": {
                    "case_count": len(grades),
                    "passed_count": len(grades) - len(failures),
                    "failed_count": len(failures),
                    "ready": self.training_result()["ready"],
                    "stagnation_review_required": self._repair_count >= 3,
                    "baseline_review_required": self._baseline_repair_count >= 2,
                },
                "run_results": failures,
            }
        )

    def _activate(self, case_id: str) -> None:
        self.validation.active_id = case_id
        self.validation.frozen = PageBridge(
            lambda: DeviceError("frozen_page", "Cannot capture a frozen page")
        )
        self.validation.frozen.load_snapshot(self.validation.cases[case_id]["snapshot"])
        self.validation.marked.clear()

    def list_validation_cases(self) -> str:
        """List this run's cases for the current page, without internal XML or baseline images."""
        return encoded(
            [
                {
                    "case_id": key,
                    "historical": key in self.validation.historical_ids,
                    "grade": self.validation.grades.get(key),
                    "guide_fingerprint": case["guide_fingerprint"],
                }
                for key, case in self.validation.cases.items()
            ]
        )

    def load_validation_case(self, case_id: str) -> str:
        """Load a frozen case for offline inspection and reannotation; cannot operate the device."""
        if self.validation.active_id and self.validation.active_id not in self.validation.historical_ids:
            if self.validation.cases[self.validation.active_id]["baseline"] is None:
                raise ValueError("Save the active visual baseline before switching cases")
        if case_id not in self.validation.cases:
            raise ValueError("Unknown case ID")
        self._activate(case_id)
        return self._page_call("page_view", {})

    def return_to_live_page(self) -> str:
        """Leave a saved visual case without moving the device; failed cases remain in regression."""
        if self.validation.active_id and self.validation.cases[self.validation.active_id]["baseline"] is None:
            raise ValueError("Save the current visual baseline before leaving the case")
        self.validation.active_id = None
        self.validation.frozen = None
        self.validation.marked.clear()
        return encoded(self.event_hub.page_bridge.query("page_view", {})[0])

    def abandon_validation(self, reason: Literal["unreadable_screenshot", "snapshot_mismatch"]) -> str:
        """Discard an inconclusive new screenshot sample before baseline acceptance."""
        if (
            self.validation.active_id is None
            or self.validation.active_id in self.validation.historical_ids
            or self.validation.cases[self.validation.active_id]["baseline"] is not None
        ):
            raise ValueError("Cannot abandon a scored or historical case")
        del self.validation.cases[self.validation.active_id]
        self.validation.active_id = None
        self.validation.frozen = None
        self.validation.marked.clear()
        return encoded({"abandoned": True, "reason": reason})

    def save_merchant_profile(self, merchant: str) -> str:
        """Save merchant identity from initial top-state screenshot and page evidence."""
        if (
            self._revision
            or not self._profile_screenshot
            or self.validation.active_id in self.validation.historical_ids
        ):
            raise ValueError("Save merchant profile after an initial screenshot and before swiping")
        profile: JsonObject = object_value(merchant)
        name: JsonValue = profile.get("name")
        if shape_errors(self.merchant_schema, profile) or not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid merchant profile or empty name")
        self._profile = profile
        return encoded({"ok": True, "saved": True})

    def merchant_profile(self) -> JsonObject:
        return dict(self._profile)

    def training_result(self) -> TrainingResult:
        current: list[ValidationCase] = [
            case for key, case in self.validation.cases.items() if key not in self.validation.historical_ids
        ]
        try:
            fresh: bool = self.validation.tested_stamp == self._stamp()
        except (ValueError, FileNotFoundError):
            fresh = False
        passing: bool = (
            fresh
            and bool(self.validation.grades)
            and all(grade["score"] == 100 for grade in self.validation.grades.values())
        )
        ready: bool = bool(
            passing
            and len({fingerprint(case["snapshot"]["xml"]) for case in current}) >= 2
            and len({case["revision"] for case in current}) >= 2
            and any(case["random_state"] for case in current)
            and (not self.requires_merchant_profile or self._profile.get("name"))
        )
        profile: NormalSwipeProfile | None = self._swipe_profile() if passing else None
        return {
            "ready": ready,
            "score": min((g["score"] for g in self.validation.grades.values()), default=0) if fresh else 0,
            "validated_states": len(current) if passing else 0,
            "script_fingerprint": self._script_fingerprint(),
            "normal_swipe_profile": profile,
        }

    def _swipe_profile(self) -> NormalSwipeProfile | None:
        samples: list[NormalSwipeSample] = [self._swipe_samples[key] for key in self._confirmed_swipes]
        revisions: set[int] = {
            case["revision"]
            for key, case in self.validation.cases.items()
            if key not in self.validation.historical_ids
            and key in self.validation.grades
            and self.validation.grades[key]["score"] == 100
        }
        if (
            len(samples) < 2
            or {sample.action for sample in samples} != {"swipe_up", "swipe_up_long"}
            or any(sample.revision not in revisions for sample in samples)
        ):
            return None
        # Samples use whole-page view_update_ratio, matching collection_swipe.
        threshold: float = min(1.0, max(sample.ratio for sample in samples) + 0.05)
        return {
            "change_threshold": threshold,
            "sample_count": len(samples),
            "actions": sorted({sample.action for sample in samples}),
            "warning": "Threshold is 1; view update ratio cannot detect excessive changes."
            if threshold == 1
            else "",
        }

    def confirm_normal_swipes(self, operation_ids: list[str]) -> str:
        """Confirm normal live swipes and train the view update ratio threshold.

        Include at least one short and one long upward swipe operation_id. Each resulting
        state needs a current, passing screenshot baseline; random and historical states do not count.
        """
        if (
            self.validation.tested_stamp != self._stamp()
            or not self.validation.grades
            or any(grade["score"] != 100 for grade in self.validation.grades.values())
        ):
            raise ValueError("Run full visual regression successfully before confirming swipes")
        selected: tuple[str, ...] = tuple(dict.fromkeys(operation_ids))
        if any(key not in self._swipe_samples for key in selected):
            raise ValueError("Only live upward swipe operation IDs from this visit are allowed")
        previous: tuple[str, ...] = self._confirmed_swipes
        self._confirmed_swipes = selected
        profile: NormalSwipeProfile | None = self._swipe_profile()
        if profile is None:
            self._confirmed_swipes = previous
            raise ValueError("Passing live states must cover both short and long normal swipes")
        return encoded({"ok": True, **profile})

    def collector_tap(self, x: int, y: int) -> str:
        """After all cases pass, tap a same-page top-reset control; returns page_change_hint IDs."""
        if (
            self.validation.active_id is not None
            or not self.requires_merchant_profile
            or self._restored
            or not self.training_result()["ready"]
        ):
            raise ValueError("Reset tap is locked until full visual regression passes")
        self._tap_attempted = True
        page, artifact = self.event_hub.tap(x, y)
        self._restored = artifact is not None
        self.validation.active_id = None
        self.validation.frozen = None
        self.validation.marked.clear()
        return encoded(page)

    def restore_to_top(self) -> RestorationResult:
        restored: bool = self._restored if self.requires_merchant_profile else True
        try:
            ReadOnlyPageView(self.event_hub.page_bridge.snapshot()).require_aliases(
                self._guide()["required_aliases"]
            )
        except (ValueError, FileNotFoundError):
            restored = False
        return {"restored": restored, "strategy": "reset_tap", "tap_attempted": self._tap_attempted}

    def persist_history(self) -> list[str]:
        if not self.training_result()["ready"] or not self.restore_to_top()["restored"]:
            raise ValueError("Only fully trained and restored visits may enter history")
        return self._save_cases()

    def _save_cases(self) -> list[str]:
        """Persist visual evidence independently of script correctness."""
        cases: list[ValidationCase] = [
            {**case, "guide": self._guide()["content"]}
            for case in self.validation.cases.values()
            if case["baseline"] is not None and case.get("image")
        ]
        return self.history.commit(
            self.page_path,
            self.page_visit_id,
            [c for c in cases if c["case_id"] not in self.validation.historical_ids],
            [c for c in cases if c["case_id"] in self.validation.historical_ids],
            run_id=self.run_id,
        )

    def _safe[**P, R](self, function: Callable[P, R]) -> Callable[P, R | str]:
        @wraps(function)
        def invoke(*args: P.args, **kwargs: P.kwargs) -> R | str:
            with self._lock:
                try:
                    return function(*args, **kwargs)
                except Exception as exc:
                    return encoded({"ok": False, "error_type": type(exc).__name__, "error": str(exc)})

        return invoke

    def create_tools(self) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        for spec in TOOLS:

            def bind(name: str) -> Callable[..., str]:
                def invoke(**kwargs: JsonValue) -> str:
                    return self._page_call(name, kwargs)

                return self._safe(invoke)

            tools.append(
                StructuredTool(
                    name=spec["name"],
                    description=spec["description"],
                    args_schema=spec["input_schema"],
                    func=bind(spec["name"]),
                )
            )
        tools.append(create_tool(self._safe(self.read_collection_guide)))
        tools.append(create_tool(self._safe(self.write_collection_guide)))
        tools.append(create_tool(self._safe(self.confirm_normal_swipes)))
        tools.append(create_tool(self._safe(self.read_collection_contract)))
        tools.append(create_tool(self._safe(self.save_collection_contract)))
        tools.append(create_tool(self._safe(self.read_run_agent_py)))
        tools.append(create_tool(self._safe(self.apply_run_agent_py_patch)))
        tools.append(create_tool(self._safe(self.collector_swipe_up)))
        tools.append(create_tool(self._safe(self.collector_swipe_up_long)))
        tools.append(create_tool(self._safe(self.collector_swipe_down)))
        tools.append(create_tool(self._safe(self.collector_swipe_to_random)))
        tools.append(create_tool(self._safe(self.collector_tap)))
        tools.append(create_tool(self._safe(self.capture_validation_case)))
        tools.append(create_tool(self._safe(self.sniff_run)))
        tools.append(create_tool(self._safe(self.screenshot)))
        tools.append(create_tool(self._safe(self.save_baseline)))
        tools.append(create_tool(self._safe(self.repair_baseline)))
        tools.append(create_tool(self._safe(self.save_merchant_profile)))
        tools.append(create_tool(self._safe(self.list_validation_cases)))
        tools.append(create_tool(self._safe(self.load_validation_case)))
        tools.append(create_tool(self._safe(self.return_to_live_page)))
        tools.append(create_tool(self._safe(self.abandon_validation)))
        return tools
