"""Page-bound collector construction, separate from generic task communication."""

import json

from langchain_core.language_models import BaseChatModel

from src.agent_runtime.contracts import AgentHandle
from src.conductor.state import ConductorState, current_visit
from src.device import DeviceOperationService
from src.page.mechanism import PageMechanism
from src.strategy.general.state import unconfirmed_arrival
from src.strategy.merchant.state import is_merchant_visit

from .engine import CollectorEngine
from .history import CollectorHistory
from .toolbox import CollectorToolbox
from .training import CollectorTrainingRegistry


class CollectorTrainingCoordinator:
    """Own shared training evidence; spawn a worker without submitting a task."""

    def __init__(self, *, model: str | BaseChatModel, mechanism: PageMechanism,
                 event_hub: DeviceOperationService, handle: AgentHandle) -> None:
        self._handle = handle
        self.model = model
        self.mechanism = mechanism
        self.event_hub = event_hub
        self.training_registry = CollectorTrainingRegistry()
        self.history = CollectorHistory()

    def spawn(self, state: ConductorState) -> str:
        if unconfirmed_arrival(state["general"]) is not None:
            raise ValueError("Confirm the current page before spawning its collector")
        visit = current_visit(state)
        path = self.mechanism.validate_page_manual(visit.get("path", ""))
        visit_id = visit.get("visit_id", "")
        run_id = state["probe"].get("run_id", "")
        if not visit_id or not run_id:
            raise ValueError("Collector requires current page_visit_id and probe_run_id")
        merchant_schema = self.mechanism.read_schema(path, "Merchant Schema")
        collection_schema = self.mechanism.read_schema(path, "Collection Schema")
        toolbox = CollectorToolbox(
            mechanism=self.mechanism, event_hub=self.event_hub, page_path=path,
            merchant_schema=json.dumps(merchant_schema, ensure_ascii=False),
            collection_schema=collection_schema, requires_merchant_profile=is_merchant_visit(visit),
            history=self.history, page_visit_id=visit_id, run_id=run_id,
        )
        with self._handle.spawner.child() as handle:
            engine = CollectorEngine(
                model=self.model, toolbox=toolbox, handle=handle, registry=self.training_registry,
                session_id=f"collector:{visit_id}", max_iterations=1000,
            )
            self.training_registry.register_visit(visit_id, path)
            self.training_registry.mark_untrained(visit_id, path)
            self._handle.spawner.start(engine)
        return handle.agent_id
