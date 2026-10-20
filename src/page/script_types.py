"""Validated page script execution and source-loading results."""

from typing import Literal, NotRequired, TypedDict

from pydantic import TypeAdapter

from src.common.utils.json import JsonObject


class ScriptSuccess(TypedDict):
    ok: Literal[True]
    canonical_path: str
    result: JsonObject


class ScriptFailure(TypedDict):
    ok: Literal[False]
    canonical_path: str
    error_type: str
    error: str
    traceback: str


type ScriptResult = ScriptSuccess | ScriptFailure


class ScriptSource(TypedDict):
    ok: Literal[True]
    canonical_path: str
    path: str
    content: str


class ScriptSourceError(TypedDict):
    ok: Literal[False]
    canonical_path: str
    error_type: str
    error: str


type ScriptSourceResult = ScriptSource | ScriptSourceError


class PatchReportBase(TypedDict):
    canonical_path: str
    script_fingerprint: str
    stagnation_review_required: NotRequired[bool]


class PatchSuccess(PatchReportBase):
    ok: Literal[True]
    applied: Literal[True]
    path: str
    bytes_before: int
    bytes_after: int


class PatchFailure(PatchReportBase):
    ok: Literal[False]
    applied: Literal[False]
    error_type: str
    error: str
    lineno: NotRequired[int | None]
    offset: NotRequired[int | None]


type PatchReport = PatchSuccess | PatchFailure

_PATCH_REPORT_ADAPTER: TypeAdapter[PatchReport] = TypeAdapter(PatchReport)


def read_patch_report(data: str) -> PatchReport:
    return _PATCH_REPORT_ADAPTER.validate_json(data, strict=True)
