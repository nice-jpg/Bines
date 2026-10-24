"""Probe collections retain their strict schema across checkpoint serialization."""

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter

from src.conductor.state import (
    ConductorState,
    ConductorUpdate,
    ProbeState,
    initial_probe_state,
    read_conductor_state,
)


def test_probe_serializer_roundtrip_preserves_collection_types() -> None:
    probe: ProbeState = initial_probe_state(("app/shop", "app/shop"))["probe"]
    probe["completed_merchants"] = {"app/shop": ["Merchant"]}
    probe["visit_history"] = [{"visit_id": "visit", "path": "app/shop", "event": "entered"}]
    saver: InMemorySaver = InMemorySaver()
    restored: object = saver.serde.loads_typed(saver.serde.dumps_typed(probe))
    assert TypeAdapter(ProbeState).validate_python(restored, strict=True) == probe
    assert probe["target_leaf_paths"] == ["app/shop"]


def test_probe_state_survives_real_graph_resume() -> None:
    observed: list[ProbeState] = []

    def validate(state: ConductorState) -> ConductorUpdate:
        current: ConductorState = TypeAdapter(ConductorState).validate_python(state, strict=True)
        observed.append(current["probe"])
        return {}

    builder: StateGraph = StateGraph(ConductorState)
    builder.add_node("validate", validate)
    builder.add_edge(START, "validate")
    builder.add_edge("validate", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config: RunnableConfig = {"configurable": {"thread_id": "probe-roundtrip"}}
    initial: ConductorState = read_conductor_state(initial_probe_state(("app/shop",)))
    initial["probe"]["completed_merchants"] = {"app/shop": ["Merchant"]}
    initial["probe"]["visit_history"] = [
        {"visit_id": "visit", "path": "app/shop", "event": "entered"}
    ]
    graph.invoke(initial, config, durability="exit")
    restored = graph.get_state(config)
    assert read_conductor_state(restored.values)["probe"] == initial["probe"]
    graph.invoke({"messages": [HumanMessage("continue")]}, config, durability="exit")
    assert observed == [initial["probe"], initial["probe"]]
