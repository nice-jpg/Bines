#!/usr/bin/env python3
"""CLI for loading and controlling a slave exposed as module:attribute."""

from __future__ import annotations

import argparse
import importlib
from typing import Any

from mind_controller_agent import MasterConfig, run_master


def _load_slave(spec: str) -> Any:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("--slave must use module:attribute syntax")
    value = getattr(importlib.import_module(module_name), attribute)
    return value() if isinstance(value, type) else value


def main() -> None:
    parser = argparse.ArgumentParser(description="Improve a slave agent through its prompts.")
    parser.add_argument("--slave", required=True, help="Slave object/class as module:attribute.")
    parser.add_argument("--max-rounds", type=int, default=10)
    parser.add_argument("--target-score", type=int)
    parser.add_argument("--stale-rounds", type=int, default=3)
    parser.add_argument("--model", default="gpt-5.4")
    args = parser.parse_args()

    result = run_master(
        _load_slave(args.slave),
        config=MasterConfig(
            max_rounds=args.max_rounds,
            target_score=args.target_score,
            stale_rounds=args.stale_rounds,
            model=args.model,
        ),
    )
    print(f"best_score={result.best_score}")
    print(f"best_round={result.best_round}")
    print(f"rounds={len(result.rounds)}")


if __name__ == "__main__":
    main()

