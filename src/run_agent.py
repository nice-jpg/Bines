"""CLI example for the LangChain create_agent harness."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from langchain_core.tools import tool

try:
    from src.agent import run_agent_loop
    from src.model import build_model
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from agent import run_agent_loop
    from model import build_model


@tool
def current_utc_time() -> str:
    """Return the current UTC time in ISO 8601 format."""

    return datetime.now(timezone.utc).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a LangChain create_agent tool-calling harness.")
    return parser


def main() -> None:
    model = build_model()
    result = run_agent_loop(
        model=model,
        tools=[current_utc_time],
        max_iterations=1000,
    )
    print(result.output)


if __name__ == "__main__":
    main()
