"""Main optimizer loop built around LangChain create_agent."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from .dumper import DumperConfig, dump_full_xml
from .middleware import OptimizerTrace, OptimizerTraceMiddleware, RecognizerCommunicationMiddleware
from .models import OptimizationRound, OptimizerRunResult, RecognizerResult
from .optimizer_script import load_optimizer, read_optimizer_source, write_optimizer_source
from .prompts import MAIN_SYSTEM_PROMPT, build_optimizer_feedback_prompt
from .scorer import score_regions


@dataclass(frozen=True)
class OptimizerConfig:
    adb: str = "adb"
    remote_output: str = "/sdcard/nice-dumper-agent/full.xml"
    timeout_ms: int = 15000
    max_rounds: int = 10
    min_growth: float = 1.0
    stale_rounds: int = 3
    output: Path = Path("workspace/optimize_xml.py")
    model: str | None = None
    fixture_xml: Path | None = None


def run_optimizer(config: OptimizerConfig) -> OptimizerRunResult:
    """Run capture, recognition, scoring, and optimizer-script iteration."""

    if config.max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")
    model = build_model(config.model)
    trace = OptimizerTrace()
    recognizer_bridge = RecognizerCommunicationMiddleware(model)
    main_agent = build_main_agent(model, trace, recognizer_bridge)

    xml0 = dump_full_xml(
        DumperConfig(
            adb=config.adb,
            remote_output=config.remote_output,
            timeout_ms=config.timeout_ms,
            fixture_xml=config.fixture_xml,
        )
    )
    l0 = recognizer_bridge.call_recognizer(xml0)
    _require_recognizer_ok("L0", l0)

    best_score = float("-inf")
    stale_count = 0

    for index in range(1, config.max_rounds + 1):
        optimizer = load_optimizer(config.output)
        optimizer_source = read_optimizer_source(config.output)
        xml1 = str(optimizer(xml0))
        l1 = recognizer_bridge.call_recognizer(xml1)
        _require_recognizer_ok("L1", l1)
        score = score_regions(xml0, xml1, l0, l1)
        suggestion = request_optimizer_source(
            main_agent=main_agent,
            xml0=xml0,
            xml1=xml1,
            l0=l0,
            l1=l1,
            score=score,
            optimizer_source=optimizer_source,
        )
        round_result = OptimizationRound(
            index=index,
            xml0_length=len(xml0),
            xml1_length=len(xml1),
            l0_count=len(l0.functions),
            l1_count=len(l1.functions),
            score=score,
            suggestion=suggestion,
        )
        trace.append(round_result)

        growth = score.score - best_score if best_score != float("-inf") else score.score
        if score.score > best_score:
            best_score = score.score
        if growth < config.min_growth:
            stale_count += 1
        else:
            stale_count = 0

        if suggestion.strip() and suggestion.strip() != optimizer_source.strip():
            write_optimizer_source(config.output, suggestion)
        else:
            stale_count += 1
        if stale_count >= config.stale_rounds:
            break

    return OptimizerRunResult(
        optimizer_path=str(config.output),
        best_score=best_score,
        rounds=list(trace.rounds),
    )


def build_main_agent(model: Any, trace: OptimizerTrace, recognizer_bridge: RecognizerCommunicationMiddleware):
    from langchain.agents import create_agent

    return create_agent(
        model=model,
        tools=[],
        system_prompt=MAIN_SYSTEM_PROMPT,
        name="xml_optimizer",
        middleware=[OptimizerTraceMiddleware(trace), recognizer_bridge],
    )


def build_model(model_name: str | None = None) -> Any:
    """Build an OpenAI-compatible ChatOpenAI model or return a LangChain model string."""

    env_path = Path(__file__).resolve().parents[2] / "workspace" / ".env"
    load_dotenv(env_path, override=False)
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL")
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEYs")
    selected_model = model_name or os.getenv("OPENAI_MODEL") or os.getenv("DEEPSEEK_MODEL")
    if base_url and api_key and selected_model:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(base_url=base_url, api_key=api_key, model=selected_model, streaming=False)
    if selected_model:
        return selected_model
    raise RuntimeError("Missing model. Provide --model or OPENAI/DEEPSEEK environment settings.")


def request_optimizer_source(
    *,
    main_agent: Any,
    xml0: str,
    xml1: str,
    l0: RecognizerResult,
    l1: RecognizerResult,
    score: Any,
    optimizer_source: str,
) -> str:
    prompt = build_optimizer_feedback_prompt(
        xml0=xml0,
        xml1=xml1,
        l0_json=_recognizer_json(l0),
        l1_json=_recognizer_json(l1),
        score_json=json.dumps(asdict(score), ensure_ascii=False, indent=2),
        optimizer_source=optimizer_source,
    )
    try:
        state = main_agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    except Exception:  # noqa: BLE001 - keep the current optimizer when the LLM is unavailable.
        return optimizer_source
    return _latest_text(state)


def _recognizer_json(result: RecognizerResult) -> str:
    return json.dumps({"functions": [asdict(item) for item in result.functions]}, ensure_ascii=False, indent=2)


def _require_recognizer_ok(name: str, result: RecognizerResult) -> None:
    if not result.ok:
        raise RuntimeError(f"{name} recognizer failed: {result.error}\n{result.raw_output}")


def _latest_text(state: Mapping[str, Any]) -> str:
    messages = state.get("messages") if isinstance(state, Mapping) else None
    if not messages:
        return ""
    latest = messages[-1]
    content = getattr(latest, "content", None)
    if content is None and isinstance(latest, Mapping):
        content = latest.get("content")
    return content if isinstance(content, str) else str(content or "")
