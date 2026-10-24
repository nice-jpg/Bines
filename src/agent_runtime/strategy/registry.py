"""Per-graph registration, schema validation and independent initial state."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Annotated, get_args, get_origin, get_type_hints

from langchain.agents.middleware.types import AgentState
from pydantic import TypeAdapter
from typing_extensions import is_typeddict

from src.common.utils.validation import is_string_mapping

from .base import Strategy
from .registration import StrategyRegistration


class StrategyRegistry[StateT: AgentState]:
    def __init__(self) -> None:
        self._registrations: dict[str, StrategyRegistration[StateT]] = {}
        self._frozen: bool = False
        self._required_keys: dict[str, frozenset[str]] = {}

    def register(self, registration: StrategyRegistration[StateT]) -> None:
        if self._frozen:
            raise RuntimeError("Strategy registry is frozen")
        name: str = registration.name
        if (
            not name.isidentifier()
            or name in get_type_hints(AgentState)
            or name in {"jump_to", "__interrupt__", "active_strategy"}
        ):
            raise ValueError(f"Invalid or reserved strategy name: {name}")
        if name in self._registrations:
            raise ValueError(f"Duplicate strategy: {name}")
        if not is_typeddict(registration.state_schema):
            raise TypeError(f"Strategy {name} requires a TypedDict state schema")
        self._registrations[name] = registration

    def validate_schema(self, state_schema: type[StateT]) -> None:
        if self._frozen:
            raise RuntimeError("Strategy registry is frozen")
        hints: dict[str, object] = get_type_hints(state_schema, include_extras=True)
        if hints.get("active_strategy") is not str:
            raise TypeError("Root state must declare active_strategy: str")
        for name, registration in self._registrations.items():
            field: object = hints.get(name)
            if get_origin(field) is Annotated:
                field = get_args(field)[0]
            if field is not registration.state_schema:
                raise TypeError(f"Root state must declare {name}: {registration.state_schema.__name__}")
            initial: Mapping[str, object] = registration.initial_state()
            self._required_keys[name] = frozenset(initial)
            self._validate_value(registration, initial)
        self._frozen = True

    def _validate_value(self, registration: StrategyRegistration[StateT], value: object) -> None:
        if not is_string_mapping(value):
            raise TypeError(f"Strategy {registration.name} requires a mapping")
        missing: frozenset[str] = self._required_keys[registration.name].difference(value)
        if missing:
            raise ValueError(f"Incomplete strategy state {registration.name}: {sorted(missing)}")
        adapter: TypeAdapter[object] = TypeAdapter(registration.state_schema)
        adapter.validate_python(value, strict=True)

    def initial_state(self, existing: Mapping[str, object] | None = None) -> dict[str, object]:
        if not self._frozen:
            raise RuntimeError("Validate the strategy schema before initializing state")
        values: Mapping[str, object] = existing if existing is not None else {}
        update: dict[str, object] = {}
        for name, registration in self._registrations.items():
            if name in values:
                self._validate_value(registration, values[name])
            else:
                initial: Mapping[str, object] = registration.initial_state()
                self._validate_value(registration, initial)
                update[name] = deepcopy(dict(initial))
        return update

    def get(self, name: str) -> Strategy[StateT]:
        try:
            return self._registrations[name].strategy
        except KeyError as error:
            raise ValueError(f"Unregistered strategy: {name}") from error

    def inactive_strategies(self, selected: str) -> tuple[Strategy[StateT], ...]:
        return tuple(
            registration.strategy for name, registration in self._registrations.items() if name != selected
        )
