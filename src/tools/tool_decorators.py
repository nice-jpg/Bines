"""Method-level declarations for EventHub tool behavior."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
import inspect
from typing import Any, TypeVar, cast

try:
    from src.tools.safety import DangerousActionJustification, DangerousToolPolicy
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from tools.safety import DangerousActionJustification, DangerousToolPolicy


F = TypeVar("F", bound=Callable[..., Any])
_DANGEROUS_POLICY_ATTR = "__bines_dangerous_tool_policy__"
_TOOL_CALLABLE_ADAPTER_ATTR = "__bines_tool_callable_adapter__"


def dangerous_func(func: F) -> F:
    """Declare that a method needs structured justification as a model tool."""

    policy_id = f"{func.__module__}.{func.__qualname__}"
    setattr(func, _DANGEROUS_POLICY_ATTR, DangerousToolPolicy(policy_id=policy_id))
    setattr(func, _TOOL_CALLABLE_ADAPTER_ATTR, _dangerous_tool_callable)
    return func


def xy_operation(func: F) -> F:
    """Declare that a method is a coordinate-based page operation."""

    setattr(func, _TOOL_CALLABLE_ADAPTER_ATTR, _xy_tool_callable)
    return func


def dangerous_policy_for(func: Callable[..., Any]) -> DangerousToolPolicy | None:
    """Read a policy attached by ``dangerous_func``."""

    policy = getattr(func, _DANGEROUS_POLICY_ATTR, None)
    return policy if isinstance(policy, DangerousToolPolicy) else None


def is_xy_operation(func: Callable[..., Any]) -> bool:
    """Return whether a method was declared with ``xy_operation``."""

    return getattr(func, _TOOL_CALLABLE_ADAPTER_ATTR, None) is _xy_tool_callable


def model_tool_callable(func: Callable[..., Any]) -> Callable[..., Any]:
    """Build the model-facing callable while preserving direct method APIs."""

    adapter = getattr(func, _TOOL_CALLABLE_ADAPTER_ATTR, None)
    return adapter(func) if callable(adapter) else func


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
        **getattr(func, "__annotations__", {}),
        "justification": DangerousActionJustification,
    }
    guarded.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    return cast(Callable[..., Any], guarded)


def _xy_tool_callable(func: Callable[..., Any]) -> Callable[[int, int], Any]:
    """Expose a bound coordinate operation with an explicit model signature."""

    def coordinate_call(x: int, y: int) -> Any:
        return func(x, y)

    coordinate_call.__name__ = func.__name__
    coordinate_call.__doc__ = func.__doc__
    return coordinate_call
