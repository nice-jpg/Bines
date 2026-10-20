"""Page-local item identity and completeness rules, independent of extractors."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict

from src.common.utils.json import JsonObject, JsonValue, is_empty_json, json_object


class CollectionContractData(TypedDict):
    primary_key: list[str]
    required_fields: list[str]


def _field_names(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Contract fields must be lists of nonempty field names")
    fields: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Contract fields must be lists of nonempty field names")
        fields.append(item)
    return fields


@dataclass(frozen=True)
class CollectionContract:
    primary_key: tuple[str, ...]
    required_fields: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: object) -> CollectionContract:
        try:
            data: JsonObject = json_object(value)
        except TypeError as exc:
            raise ValueError("Collection contract must be an object") from exc
        primary: list[str] = _field_names(data.get("primary_key"))
        required: list[str] = _field_names(data.get("required_fields"))
        if not primary:
            raise ValueError("At least one primary key field is required")
        return cls(tuple(sorted(set(primary))), tuple(sorted(set(primary + required))))

    def as_dict(self) -> CollectionContractData:
        return {"primary_key": list(self.primary_key), "required_fields": list(self.required_fields)}

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()

    def missing(self, item: Mapping[str, JsonValue]) -> list[str]:
        return [field for field in self.required_fields if is_empty_json(item.get(field))]

    def key(self, item: Mapping[str, JsonValue]) -> tuple[str, ...]:
        def cell(value: JsonValue) -> str:
            return (
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, dict | list)
                else str(value)
            )

        return tuple(cell(item.get(field, "")).strip() for field in self.primary_key)
