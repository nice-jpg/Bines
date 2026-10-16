"""Device failures with an explicit serialization boundary."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from src.json_types import JsonObject, json_object


@dataclass(frozen=True)
class DeviceError:
    error_type: str
    message: str
    details: JsonObject = field(default_factory=lambda: {})

    def as_dict(self) -> JsonObject:
        return {"ok": False, "error_type": self.error_type, "message": self.message, "details": self.details}


def make_error_result(
    error_type: str, message: str, *, details: Mapping[str, object] | None = None
) -> DeviceError:
    return DeviceError(error_type, message, {} if details is None else json_object(details))


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    error: DeviceError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    def check_returncode(self) -> DeviceError | None:
        return self.error
