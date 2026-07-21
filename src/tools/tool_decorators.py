"""Method-level declarations for EventHub tool behavior."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
import inspect
from typing import Any, TypeVar, cast

if __package__ and __package__.startswith("src."):
    from src.tools.safety import DangerousActionJustification, DangerousToolPolicy
else:  # Supports running tests and python src/run_agent.py with src on sys.path.
    from tools.safety import DangerousActionJustification, DangerousToolPolicy


F = TypeVar("F", bound=Callable[..., Any])
_DECLARATION_ATTRIBUTE = "__bines_tool_declaration__"
_DANGEROUS_POLICY_ATTRIBUTE = "__bines_dangerous_policy__"
_DANGEROUS_DECLARATION = "dangerous"
_XY_DECLARATION = "xy"


def dangerous_func(func: F) -> F:
    """Declare that a method needs structured justification as a model tool."""

    policy_id = f"{func.__module__}.{func.__qualname__}"
    func.__dict__[_DANGEROUS_POLICY_ATTRIBUTE] = DangerousToolPolicy(policy_id=policy_id)
    func.__dict__[_DECLARATION_ATTRIBUTE] = _DANGEROUS_DECLARATION
    return func


def xy_operation(func: F) -> F:
    """Declare that a method is a coordinate-based page operation."""

    func.__dict__[_DECLARATION_ATTRIBUTE] = _XY_DECLARATION
    return func


def dangerous_policy_for(func: Callable[..., Any]) -> DangerousToolPolicy | None:
    """Read a policy attached by ``dangerous_func``."""

    policy = _declaration_target(func).__dict__.get(_DANGEROUS_POLICY_ATTRIBUTE)
    return policy if isinstance(policy, DangerousToolPolicy) else None


def is_xy_operation(func: Callable[..., Any]) -> bool:
    """Return whether a method was declared with ``xy_operation``."""

    return _declaration_target(func).__dict__.get(_DECLARATION_ATTRIBUTE) == _XY_DECLARATION


def model_tool_callable(func: Callable[..., Any]) -> Callable[..., Any]:
    """Build the model-facing callable while preserving direct method APIs."""

    declaration = _declaration_target(func).__dict__.get(_DECLARATION_ATTRIBUTE)
    if declaration == _DANGEROUS_DECLARATION:
        return _dangerous_tool_callable(func)
    if declaration == _XY_DECLARATION:
        return _xy_tool_callable(func)
    return func


def _dangerous_tool_callable(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    def guarded(*args: Any, justification: DangerousActionJustification, **kwargs: Any) -> Any:
        DangerousActionJustification.model_validate(justification)
        return func(*args, **kwargs)

    signature = inspect.signature(func)
    parameters = list(signature.parameters.values())
    justification_parameter = inspect.Parameter(
        "justification",
        kind=inspect.Parameter.KEYWORD_ONLY,
        annotation=DangerousActionJustification,
    )
    var_keyword_index = next(
        (
            index
            for index, parameter in enumerate(parameters)
            if parameter.kind is inspect.Parameter.VAR_KEYWORD
        ),
        len(parameters),
    )
    parameters.insert(var_keyword_index, justification_parameter)
    guarded.__annotations__ = {
        **inspect.get_annotations(func),
        "justification": DangerousActionJustification,
    }
    guarded.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    return cast(Callable[..., Any], guarded)


def _declaration_target(func: Callable[..., Any]) -> Callable[..., Any]:
    return func.__func__ if inspect.ismethod(func) else func


def _xy_tool_callable(func: Callable[..., Any]) -> Callable[[int, int], Any]:
    """Expose a bound coordinate operation with an explicit model signature."""

    def coordinate_call(x: int, y: int) -> Any:
        return func(x, y)

    coordinate_call.__name__ = func.__name__
    coordinate_call.__doc__ = func.__doc__
    return coordinate_call
