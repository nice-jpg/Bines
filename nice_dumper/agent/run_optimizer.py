#!/usr/bin/env python3
"""CLI for the nice_dumper XML optimizer agent."""

from __future__ import annotations

import argparse
from pathlib import Path

from nice_dumper_agent.main_agent import OptimizerConfig, run_optimizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the nice_dumper XML optimizer agent.")
    parser.add_argument("--adb", default="adb", help="adb executable path.")
    parser.add_argument(
        "--remote-output",
        default="/sdcard/nice-dumper-agent/full.xml",
        help="Remote XML output path used by /data/local/tmp/project.",
    )
    parser.add_argument("--timeout-ms", type=int, default=15000, help="Dumper timeout in milliseconds.")
    parser.add_argument("--max-rounds", type=int, default=10, help="Maximum optimization rounds.")
    parser.add_argument(
        "--min-growth",
        type=float,
        default=1.0,
        help="Minimum score growth considered useful.",
    )
    parser.add_argument(
        "--stale-rounds",
        type=int,
        default=3,
        help="Stop after this many consecutive rounds without useful growth.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent / "workspace" / "optimize_xml.py"),
        help="Optimizer script path.",
    )
    parser.add_argument("--model", default=None, help="Model name or LangChain model string.")
    parser.add_argument(
        "--fixture-xml",
        default=None,
        help="Use a local XML file instead of calling adb; useful for tests and dry runs.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_optimizer(
        OptimizerConfig(
            adb=args.adb,
            remote_output=args.remote_output,
            timeout_ms=args.timeout_ms,
            max_rounds=args.max_rounds,
            min_growth=args.min_growth,
            stale_rounds=args.stale_rounds,
            output=Path(args.output),
            model=args.model,
            fixture_xml=Path(args.fixture_xml) if args.fixture_xml else None,
        )
    )
    print(f"best_score={result.best_score:.4f}")
    print(f"rounds={len(result.rounds)}")
    print(f"optimizer={result.optimizer_path}")


if __name__ == "__main__":
    main()
