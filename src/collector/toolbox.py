"""Single-agent annotation, visual evidence, script repair and regression tools."""

from datetime import datetime, timezone
from dataclasses import dataclass
from functools import wraps
import hashlib
import json
from threading import RLock
from typing import Literal
from uuid import uuid4

from langchain_core.tools import StructuredTool
from Reco.model_interface import TOOLS
from src.page_view import ReadOnlyPageView
from src.collection_contract import CollectionContract
from src.tools.screenshot import capture_png, image_url


if __package__.startswith("src."):
    from src.page_mechanism import eval_collection
    from src.tools.page_bridge import PageBridge
else:
    from page_mechanism import eval_collection
    from tools.page_bridge import PageBridge
from .history import CollectorHistory


@dataclass(frozen=True)
class NormalSwipeSample:
    operation_id: str
    action: Literal["swipe_up", "swipe_up_long"]
    revision: int
    ratio: float


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def object_value(value):
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def shape_errors(schema, value, path="result"):
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            return [path]
        if not schema:
            return []
        errors = [f"{path}.{key}" for key in set(value) ^ set(schema)]
        for key in set(value) & set(schema):
            errors.extend(shape_errors(schema[key], value[key], f"{path}.{key}"))
        return errors
    if isinstance(schema, list):
        if not isinstance(value, list):
            return [path]
        return [error for i, item in enumerate(value) for error in shape_errors(schema[0], item, f"{path}[{i}]")] if schema else []
    if schema is not None and (type(schema) is not type(value)):
        if not (type(schema) in (int, float) and type(value) in (int, float)):
            return [path]
    return []


class CollectorToolbox:
    def __init__(self, *, mechanism, event_hub, page_path, run_id, merchant_schema="{}",
                 collection_schema="{}", requires_merchant_profile=False,
                 history=None, page_visit_id=None):
        self.mechanism = mechanism
        self.event_hub = event_hub
        self.page_path = page_path
        self.page_visit_id = page_visit_id or uuid4().hex
        self.run_id = run_id
        self.merchant_schema = object_value(merchant_schema)
        self.collection_schema = object_value(collection_schema)
        self.requires_merchant_profile = requires_merchant_profile
        self.history = history or CollectorHistory()
        self.cases = {case["case_id"]: case for case in self.history.load(page_path, run_id=self.run_id)}
        self.historical_ids = set(self.cases)
        self.active_id = None
        self.frozen = None
        self._marked = set()
        self._guide_version = ""
        self._profile = {}
        self._reviewed_images = set()
        self._revision = 0
        self._swipe_samples: dict[str, NormalSwipeSample] = {}
        self._confirmed_swipes: tuple[str, ...] = ()
        self._random = False
        self._saw_screenshot = False
        self._profile_screenshot = False
        self._read_regions = False
        self._restored = False
        self._tap_attempted = False
        self._exposed_fingerprint = None
        self._repair_count = 0
        self._grades = {}
        self._tested_stamp = None
        self._lock = RLock()

    def _guide(self):
        return self.mechanism.read_collection_guide(self.page_path)

    def _script_fingerprint(self):
        return fingerprint(self.mechanism.read_run_agent_py(self.page_path))

    def _stamp(self):
        contract = self.mechanism.collection_contract(self.page_path)
        return (self._script_fingerprint(), self._guide()["fingerprint"], fingerprint(encoded(self.cases)),
                contract.fingerprint if contract else None)

    def read_collection_contract(self) -> str:
        """Read existing PAGE.md identity and completeness rules; reuse them if evidence agrees."""
        contract = self.mechanism.collection_contract(self.page_path)
        return encoded({"contract": contract.as_dict() if contract else None})

    def save_collection_contract(self, primary_key: list[str], required_fields: list[str], evidence: str = "") -> str:
        """Save missing rules or correct an evidenced counterexample; never relax rules for clipped rows."""
        contract = CollectionContract.from_dict({"primary_key": primary_key, "required_fields": required_fields})
        item_schema = self.collection_schema.get("items")
        if not isinstance(item_schema, list) or not item_schema or not isinstance(item_schema[0], dict):
            raise ValueError("collection_schema must describe item fields")
        fields = set(item_schema[0])
        if not set(contract.required_fields) <= fields:
            raise ValueError("Contract fields must exist in collection_schema.items")
        old = self.mechanism.collection_contract(self.page_path)
        if old == contract:
            return encoded({"ok": True, "updated": False})
        current = [case for key, case in self.cases.items() if key not in self.historical_ids]
        if len({case["revision"] for case in current if case["baseline"] is not None}) < 2:
            raise ValueError("Inspect and save multiple current states before defining rules")
        if old and not evidence.strip():
            raise ValueError("A concrete counterexample is required to change existing rules")
        self._validate_contract(contract)
        updated = self.mechanism.save_collection_contract(self.page_path, contract)
        return encoded({"ok": True, "updated": updated, "fingerprint": contract.fingerprint})

    def _validate_contract(self, contract):
        """Reject collisions within one visual state; clipped baseline rows are not rule counterexamples."""
        item_schema = self.collection_schema.get("items")
        if not isinstance(item_schema, list) or not item_schema or not set(contract.required_fields) <= set(item_schema[0]):
            raise ValueError("Contract fields must exist in collection_schema.items")
        for case in self.cases.values():
            seen = set()
            for item in (case["baseline"] or {}).get("items", []):
                if contract.missing(item):
                    continue
                key = contract.key(item)
                if key in seen:
                    raise ValueError(f"Nonunique visual key in case {case['case_id']}")
                seen.add(key)

    def read_collection_guide(self) -> str:
        """Read COLLECT.md and its required numeric aliases before annotating."""
        try:
            guide = self._guide()
        except FileNotFoundError:
            return encoded({"ok": False, "error_type": "guide_missing", "next": "Inspect regions and screenshot, then write_collection_guide"})
        self._guide_version = guide["fingerprint"]
        self._marked.clear()
        return encoded({"ok": True, **guide})

    def write_collection_guide(self, content: str) -> str:
        """Write English COLLECT.md. First line: JSON required_aliases, e.g. {\"required_aliases\":[\"1\"]}."""
        if not self._saw_screenshot or not self._read_regions:
            raise ValueError("Inspect region evidence and a screenshot before writing the guide")
        guide = self.mechanism.write_collection_guide(self.page_path, content)
        self._guide_version = guide["fingerprint"]
        self._marked.clear()
        return encoded({"ok": True, "fingerprint": self._guide_version, "remark_all_cases": True})

    def read_run_agent_py(self, force: bool = False) -> str:
        """Read the current script; unchanged content is omitted unless force=true."""
        result = self.mechanism.read_run_agent_py_snapshot(self.page_path)
        if not result["ok"]:
            return encoded(result)
        content = result.pop("content")
        digest = fingerprint(content)
        omitted = not force and digest == self._exposed_fingerprint
        self._exposed_fingerprint = digest
        return encoded({**result, "script_fingerprint": digest, "content_omitted": omitted,
                        "bytes": len(content.encode()), "lines": len(content.splitlines()),
                        **({} if omitted else {"content": content})})

    def apply_run_agent_py_patch(self, patch: str) -> str:
        """Apply one scoped Codex Update File patch to run.agent.py only."""
        result = json.loads(self.mechanism.apply_run_agent_py_patch(self.page_path, patch))
        if result.get("ok"):
            self._repair_count += 1
            self._exposed_fingerprint = result["script_fingerprint"]
        result["stagnation_review_required"] = self._repair_count >= 3
        return encoded(result)

    def _page_call(self, name, args):
        bridge = self.frozen or self.event_hub.page_bridge
        if name == "update_aaid":
            guide = self._guide()
            if self._guide_version != guide["fingerprint"]:
                raise ValueError("Read the current guide before assigning aliases")
        if self.active_id:
            result, _ = bridge.query(name, args)
        else:
            result, _ = bridge.call(name, args)
        if name in {"page_read", "page_node", "page_catalog"}:
            self._read_regions = True
        if name == "update_aaid":
            self._marked.add(args["value"])
            if self.active_id:
                case = self.cases[self.active_id]
                case["snapshot"] = bridge.snapshot()
                if set(guide["required_aliases"]) <= self._marked:
                    ReadOnlyPageView(case["snapshot"]).require_aliases(guide["required_aliases"])
                    case["guide_fingerprint"] = guide["fingerprint"]
        return encoded(result)

    def _move(self, action, x, y, *, random_state=False,
              normal_action: Literal["swipe_up", "swipe_up_long"] | None = None):
        if self.active_id or self._restored:
            raise ValueError("Validation is frozen; save its baseline and return_to_live_page before moving")
        if self.requires_merchant_profile and not self._profile:
            raise ValueError("Save the merchant profile from the live top-state screenshot before moving")
        page, artifact = action(x, y)
        if artifact:
            self._revision += 1
            self._random = random_state
            self._marked.clear()
            if normal_action:
                ratio = self.event_hub.page_bridge.root_change_ratio()
                if ratio is not None:
                    operation_id = uuid4().hex
                    self._swipe_samples[operation_id] = NormalSwipeSample(
                        operation_id, normal_action, self._revision, ratio)
                    page = {**page, "operation_id": operation_id}
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
        if self.active_id is not None:
            raise ValueError("Return to the live page before capturing another case")
        guide = self._guide()
        if self._guide_version != guide["fingerprint"] or not set(guide["required_aliases"]) <= self._marked:
            raise ValueError("Read the guide and explicitly mark every required alias on this state")
        snapshot = self.event_hub.page_bridge.snapshot()
        ReadOnlyPageView(snapshot).require_aliases(guide["required_aliases"])
        case_id = uuid4().hex
        self.cases[case_id] = {
            "case_id": case_id, "page_visit_id": self.page_visit_id, "run_id": self.run_id,
            "snapshot": snapshot, "baseline": None, "image": None,
            "schema": self.collection_schema, "revision": self._revision,
            "random_state": self._random, "guide_fingerprint": guide["fingerprint"],
        }
        self._activate(case_id)
        return encoded({"ok": True, "case_id": self.active_id, "awaiting": "screenshot_then_save_baseline"})

    def sniff_run(self) -> str:
        """Test all prepared cases for this page in the current probe run, without device operations."""
        if not self.cases:
            raise ValueError("Prepare visual cases before running regression")
        if any(case["baseline"] is None or not case.get("image") for case in self.cases.values()):
            raise ValueError("Finish visual baseline preparation before running regression")
        return self._evaluate()

    def screenshot(self) -> list:
        """Return actual PNG image evidence for the current live or frozen historical state."""
        case = self.cases.get(self.active_id)
        if case and case.get("image"):
            image = case["image"]
        else:
            image = image_url(capture_png(self.event_hub))
            if self._revision == 0:
                self._profile_screenshot = True
            if case:
                case["image"] = image
                case["screenshot_at"] = datetime.now(timezone.utc).isoformat()
        self._saw_screenshot = True
        if case:
            self._reviewed_images.add(self.active_id)
        return [{"type": "text", "text": encoded({"case_id": self.active_id, "schema": self.collection_schema,
                 "instruction": "Extract only visually confirmed information; do not infer from script output."})},
                {"type": "image_url", "image_url": {"url": image}}]

    def save_baseline(self, r1: str) -> str:
        """Save the independent screenshot baseline once; historical baselines are immutable."""
        if self.active_id is None or self.active_id in self.historical_ids:
            raise ValueError("A new live validation case is required")
        case = self.cases[self.active_id]
        if not case["image"] or case["baseline"] is not None:
            raise ValueError("Capture screenshot first; an accepted baseline cannot be overwritten")
        baseline = object_value(r1)
        errors = shape_errors(self.collection_schema, baseline)
        if errors:
            raise ValueError(f"Baseline does not match collection_schema: {errors}")
        case["baseline"] = baseline
        self._tested_stamp = None
        self._grades.clear()
        self._save_cases()
        return encoded({"ok": True, "case_id": self.active_id, "saved": True})

    def repair_baseline(self, r1: str, evidence: str) -> str:
        """Correct a saved baseline from its original screenshot, retaining every prior revision."""
        case = self.cases.get(self.active_id)
        if not case or case["baseline"] is None or self.active_id not in self._reviewed_images:
            raise ValueError("Load the saved case and inspect its original screenshot before repair")
        if not evidence.strip():
            raise ValueError("Describe the erroneous fields and what the original screenshot shows")
        baseline = object_value(r1)
        errors = shape_errors(case["schema"], baseline)
        if errors:
            raise ValueError(f"Baseline does not match the saved schema: {errors}")
        if baseline == case["baseline"]:
            return encoded({"ok": True, "changed": False})
        previous = dict(case)
        case["baseline_revisions"] = [*case.get("baseline_revisions", []), {
            "baseline": case["baseline"], "evidence": evidence.strip(),
            "revised_at": datetime.now(timezone.utc).isoformat(),
        }]
        case["baseline"] = baseline
        self._tested_stamp = None
        self._grades.clear()
        try:
            self._save_cases()
        except Exception:
            self.cases[self.active_id] = previous
            raise
        self._reviewed_images.discard(self.active_id)
        return encoded({"ok": True, "changed": True, "regression_required": True})

    def _evaluate(self):
        guide = self._guide()
        contract = self.mechanism.collection_contract(self.page_path)
        if contract is None:
            raise ValueError("Define the collection contract from visual evidence before regression")
        self._validate_contract(contract)
        outputs, grades = {}, {}
        for case_id, case in self.cases.items():
            result = None
            if case["guide_fingerprint"] != guide["fingerprint"]:
                grade = {"score": 0, "errors": ["remark_required"], "missing": []}
            elif case["schema"] != self.collection_schema:
                grade = {"score": 0, "errors": ["history_schema_mismatch"], "missing": []}
            elif case["baseline"] is None or not case.get("image"):
                grade = {"score": 0, "errors": ["visual_baseline_required"], "missing": []}
            else:
                result = self.mechanism.run_page_script(self.page_path, ReadOnlyPageView(case["snapshot"]))
                grade = eval_collection(case["baseline"], result)
                if not result["ok"]:
                    grade["execution_error"] = result
                elif shape_errors(self.collection_schema, result["result"]):
                    grade = {"score": 0, "errors": ["output_schema_mismatch"], "missing": []}
            outputs[case_id] = result.get("result") if result else None
            grades[case_id] = grade
        self._grades = grades
        self._tested_stamp = self._stamp()
        return encoded({
            "cases": [{"case_id": key, "historical": key in self.historical_ids,
                       "snapshot_id": case["snapshot"]["snapshot_id"]} for key, case in self.cases.items()],
            "ground_truths": {key: case["baseline"] for key, case in self.cases.items()},
            "run_results": outputs,
            "eval_grades": {"score": min((grade["score"] for grade in grades.values()), default=0),
                            "cases": grades, "ready": self.training_result()["ready"],
                            "stagnation_review_required": self._repair_count >= 3},
        })

    def _activate(self, case_id):
        self.active_id = case_id
        self.frozen = PageBridge(lambda: None)
        self.frozen.load_snapshot(self.cases[case_id]["snapshot"])
        self._marked.clear()

    def list_validation_cases(self) -> str:
        """List this run's cases for the current page, without internal XML or baseline images."""
        return encoded([{"case_id": key, "historical": key in self.historical_ids,
                         "grade": self._grades.get(key), "guide_fingerprint": case["guide_fingerprint"]}
                        for key, case in self.cases.items()])

    def load_validation_case(self, case_id: str) -> str:
        """Load a frozen case for offline inspection and reannotation; cannot operate the device."""
        if self.active_id and self.active_id not in self.historical_ids:
            if self.cases[self.active_id]["baseline"] is None:
                raise ValueError("Save the active visual baseline before switching cases")
        if case_id not in self.cases:
            raise ValueError("Unknown case ID")
        self._activate(case_id)
        return self._page_call("page_view", {})

    def return_to_live_page(self) -> str:
        """Leave a saved visual case without moving the device; failed cases remain in regression."""
        if self.active_id and self.cases[self.active_id]["baseline"] is None:
            raise ValueError("Save the current visual baseline before leaving the case")
        self.active_id = None
        self.frozen = None
        self._marked.clear()
        return encoded(self.event_hub.page_bridge.query("page_view", {})[0])

    def abandon_validation(self, reason: Literal["unreadable_screenshot", "snapshot_mismatch"]) -> str:
        """Discard an inconclusive new screenshot sample before baseline acceptance."""
        if self.active_id is None or self.active_id in self.historical_ids or self.cases[self.active_id]["baseline"] is not None:
            raise ValueError("Cannot abandon a scored or historical case")
        del self.cases[self.active_id]
        self.active_id = None
        self.frozen = None
        self._marked.clear()
        return encoded({"abandoned": True, "reason": reason})

    def save_merchant_profile(self, merchant: str) -> str:
        """Save merchant identity from initial top-state screenshot and page evidence."""
        if self._revision or not self._profile_screenshot or self.active_id in self.historical_ids:
            raise ValueError("Save merchant profile after an initial screenshot and before swiping")
        profile = object_value(merchant)
        if shape_errors(self.merchant_schema, profile) or not isinstance(profile.get("name"), str) or not profile["name"].strip():
            raise ValueError("Invalid merchant profile or empty name")
        self._profile = profile
        return encoded({"ok": True, "saved": True})

    def merchant_profile(self):
        return dict(self._profile)

    def training_result(self):
        current = [case for key, case in self.cases.items() if key not in self.historical_ids]
        try:
            fresh = self._tested_stamp == self._stamp()
        except (ValueError, FileNotFoundError):
            fresh = False
        passing = fresh and bool(self._grades) and all(grade["score"] == 100 for grade in self._grades.values())
        ready = bool(passing and len({fingerprint(case["snapshot"]["xml"]) for case in current}) >= 2
                     and len({case["revision"] for case in current}) >= 2
                     and any(case["random_state"] for case in current)
                     and (not self.requires_merchant_profile or self._profile.get("name")))
        profile = self._swipe_profile() if passing else None
        return {"ready": ready, "score": min((g["score"] for g in self._grades.values()), default=0) if fresh else 0,
                "validated_states": len(current) if passing else 0, "script_fingerprint": self._script_fingerprint(),
                "normal_swipe_profile": profile}

    def _swipe_profile(self) -> dict | None:
        samples = [self._swipe_samples[key] for key in self._confirmed_swipes]
        revisions = {case["revision"] for key, case in self.cases.items()
                     if key not in self.historical_ids and self._grades.get(key, {}).get("score") == 100}
        if (len(samples) < 2 or {sample.action for sample in samples} != {"swipe_up", "swipe_up_long"}
                or any(sample.revision not in revisions for sample in samples)):
            return None
        threshold = min(1.0, max(sample.ratio for sample in samples) + 0.05)
        return {"change_threshold": threshold, "sample_count": len(samples),
                "actions": sorted({sample.action for sample in samples}),
                "warning": "Threshold is 1; change ratio cannot detect excessive changes." if threshold == 1 else ""}

    def confirm_normal_swipes(self, operation_ids: list[str]) -> str:
        """Confirm observed normal live swipes after visual regression passes.

        Include at least one short and one long upward swipe operation_id. Each resulting
        state needs a current, passing screenshot baseline; random and historical states do not count.
        """
        if self._tested_stamp != self._stamp() or not self._grades or any(
            grade["score"] != 100 for grade in self._grades.values()
        ):
            raise ValueError("Run full visual regression successfully before confirming swipes")
        selected = tuple(dict.fromkeys(operation_ids))
        if any(key not in self._swipe_samples for key in selected):
            raise ValueError("Only live upward swipe operation IDs from this visit are allowed")
        previous = self._confirmed_swipes
        self._confirmed_swipes = selected
        profile = self._swipe_profile()
        if profile is None:
            self._confirmed_swipes = previous
            raise ValueError("Passing live states must cover both short and long normal swipes")
        return encoded({"ok": True, **profile})

    def collector_tap(self, x: int, y: int) -> str:
        """After all cases pass, tap a same-page top-reset control; returns page_change_hint IDs."""
        if self.active_id is not None or not self.requires_merchant_profile or self._restored or not self.training_result()["ready"]:
            raise ValueError("Reset tap is locked until full visual regression passes")
        self._tap_attempted = True
        page, artifact = self.event_hub.tap(x, y)
        self._restored = artifact is not None
        self.active_id = None
        self.frozen = None
        self._marked.clear()
        return encoded(page)

    def restore_to_top(self):
        restored = self._restored if self.requires_merchant_profile else True
        try:
            ReadOnlyPageView(self.event_hub.page_bridge.snapshot()).require_aliases(self._guide()["required_aliases"])
        except (ValueError, FileNotFoundError):
            restored = False
        return {"restored": restored, "strategy": "reset_tap", "tap_attempted": self._tap_attempted}

    def persist_history(self):
        if not self.training_result()["ready"] or not self.restore_to_top()["restored"]:
            raise ValueError("Only fully trained and restored visits may enter history")
        return self._save_cases()

    def _save_cases(self):
        """Persist visual evidence independently of script correctness."""
        cases = [dict(case, guide=self._guide()["content"]) for case in self.cases.values()
                 if case["baseline"] is not None and case.get("image")]
        return self.history.commit(self.page_path, self.page_visit_id,
                                   [c for c in cases if c["case_id"] not in self.historical_ids],
                                   [c for c in cases if c["case_id"] in self.historical_ids],
                                   run_id=self.run_id)

    def _safe(self, function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with self._lock:
                try:
                    return function(*args, **kwargs)
                except Exception as exc:
                    return encoded({"ok": False, "error_type": type(exc).__name__, "error": str(exc)})
        return invoke

    def create_tools(self):
        tools = []
        for spec in TOOLS:
            def bind(name):
                return self._safe(lambda **kwargs: self._page_call(name, kwargs))
            tools.append(StructuredTool(name=spec["name"], description=spec["description"],
                                        args_schema=spec["input_schema"], func=bind(spec["name"])))
        for function in (self.read_collection_guide, self.write_collection_guide,
                         self.confirm_normal_swipes,
                         self.read_collection_contract, self.save_collection_contract,
                         self.read_run_agent_py, self.apply_run_agent_py_patch,
                         self.collector_swipe_up, self.collector_swipe_up_long,
                         self.collector_swipe_down, self.collector_swipe_to_random,
                         self.collector_tap, self.capture_validation_case, self.sniff_run, self.screenshot, self.save_baseline, self.repair_baseline,
                         self.save_merchant_profile, self.list_validation_cases,
                         self.load_validation_case, self.return_to_live_page, self.abandon_validation):
            tools.append(StructuredTool.from_function(self._safe(function)))
        return tools
