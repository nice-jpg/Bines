"""Page-local item identity and completeness rules, independent of extractors."""

from dataclasses import dataclass
import hashlib
import json


def is_empty(value):
    return value is None or (isinstance(value, str) and not value.strip()) or (
        isinstance(value, (list, dict, tuple)) and not value
    )


@dataclass(frozen=True)
class CollectionContract:
    primary_key: tuple[str, ...]
    required_fields: tuple[str, ...]

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Collection contract must be an object")
        fields = [value.get("primary_key"), value.get("required_fields")]
        if any(not isinstance(group, list) or any(not isinstance(f, str) or not f.strip() for f in group) for group in fields):
            raise ValueError("Contract fields must be lists of nonempty field names")
        if not fields[0]:
            raise ValueError("At least one primary key field is required")
        return cls(tuple(sorted(set(fields[0]))), tuple(sorted(set(fields[0] + fields[1]))))

    def as_dict(self):
        return {"primary_key": list(self.primary_key), "required_fields": list(self.required_fields)}

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()

    def missing(self, item):
        return [field for field in self.required_fields if is_empty(item.get(field))]

    def key(self, item):
        # Excel cell values are strings or scalar values; preserve these identities across reloads.
        def cell(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        return tuple(cell(item.get(field, "")).strip() for field in self.primary_key)
