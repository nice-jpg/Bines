"""Command-line entry point for replayer."""

from __future__ import annotations

from pathlib import Path
import sys

from . import imports as _imports  # noqa: F401 - installs src on sys.path.
from .replay import replay_bundle
from device.results import is_error_result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m nice_auther.replayer <bundle.json>", file=sys.stderr)
        return 2
    result = replay_bundle(Path(args[0]))
    if is_error_result(result):
        print(result["message"], file=sys.stderr)
        return 1
    if result:
        print(result)
    return 0

