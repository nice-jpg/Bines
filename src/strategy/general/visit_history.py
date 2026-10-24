"""Record compact confirmed-visit facts at safe model boundaries."""

from langchain.agents.middleware.types import AgentMiddleware
from langgraph.runtime import Runtime

from src.conductor.state import ConductorState, ConductorUpdate, VisitFact, complete_update, current_visit
from src.strategy.general.state import PageVisit


class VisitHistoryMiddleware(AgentMiddleware[ConductorState, None]):
    def before_model(self, state: ConductorState, runtime: Runtime[None]) -> ConductorUpdate | None:
        history: list[VisitFact] = list(state["probe"].get("visit_history", ()))
        visit: PageVisit = current_visit(state)
        last: VisitFact | None = history[-1] if history else None
        if last is not None and last["event"] == "entered" and last["visit_id"] != visit["visit_id"]:
            history.append({**last, "event": "exited" if state["general"].get("visit") else "closed"})
        elif last is not None and last["event"] == "exited" and state["general"].get("visit") is None:
            history.append({**last, "event": "closed"})
        if visit["visit_id"] and (
            last is None or last["visit_id"] != visit["visit_id"] or last["event"] != "entered"
        ):
            history.append({"visit_id": visit["visit_id"], "path": visit["path"], "event": "entered"})
        if tuple(history) == state["probe"].get("visit_history", ()):
            return None
        return complete_update(state, {"probe": {"visit_history": tuple(history)}})

    async def abefore_model(self, state: ConductorState, runtime: Runtime[None]) -> ConductorUpdate | None:
        return self.before_model(state, runtime)
