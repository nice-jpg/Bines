"""Main optimizer loop built around LangChain create_agent."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from .dumper import DumperConfig
from .middleware import OptimizerTrace, OptimizerTraceMiddleware, SubagentLifecycleMiddleware
from .models import OptimizerProposal, OptimizerRunResult
from .optimizer_script import ensure_optimizer_script
from .prompts import MAIN_SYSTEM_PROMPT
from .provenance import commit_initial_optimizer
from .runtime import OptimizerRuntime
from src.codex import create_chat_model


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
    """Run the optimizer through one complete main-agent invocation."""

    if config.max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")
    # model = build_model(config.model)
    model = create_chat_model(model="gpt-5.4")
    trace = OptimizerTrace()
    workspace_dir = config.output.parent
    ensure_optimizer_script(config.output)
    commit_initial_optimizer(workspace_dir, config.output)
    runtime = OptimizerRuntime(
        model=model,
        dumper_config=DumperConfig(
            adb=config.adb,
            remote_output=config.remote_output,
            timeout_ms=config.timeout_ms,
            fixture_xml=config.fixture_xml,
        ),
        output=config.output,
        trace=trace,
        max_rounds=config.max_rounds,
        min_growth=config.min_growth,
        stale_rounds=config.stale_rounds,
    )
    main_agent = build_main_agent(model, trace, runtime)
    main_agent.invoke(
        {"messages": [{"role": "user", "content": _build_main_run_prompt(config)}]},
        config={"recursion_limit": max(50, config.max_rounds * 20 + 30)},
    )

    return OptimizerRunResult(
        optimizer_path=str(config.output),
        best_score=runtime.best_score,
        rounds=list(trace.rounds),
    )


def build_main_agent(model: Any, trace: OptimizerTrace, runtime: OptimizerRuntime):
    from langchain.agents import create_agent

    return create_agent(
        model=model,
        tools=runtime.build_tools(),
        system_prompt=MAIN_SYSTEM_PROMPT,
        name="xml_optimizer",
        middleware=[OptimizerTraceMiddleware(trace), SubagentLifecycleMiddleware(runtime)],
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


def parse_optimizer_proposal(raw_output: str, fallback_source: str) -> OptimizerProposal:
    """Parse model output as a reasoned optimizer proposal."""

    raw = str(raw_output or "").strip()
    if not raw:
        return OptimizerProposal(reason="Model returned empty proposal; keeping current script.", source=fallback_source)
    try:
        parsed = json.loads(_extract_json(raw))
    except json.JSONDecodeError:
        return OptimizerProposal(reason="Model returned legacy script without JSON reason.", source=_strip_fence(raw))
    if not isinstance(parsed, Mapping):
        return OptimizerProposal(reason="Model proposal was not a JSON object; keeping current script.", source=fallback_source)
    reason = str(parsed.get("reason") or "").strip() or "Model did not provide a reason."
    source = str(parsed.get("script") or "").strip()
    if not source:
        source = fallback_source
        reason = reason + " No script was provided, so the current script is kept."
    return OptimizerProposal(reason=reason, source=_strip_fence(source))


def _build_main_run_prompt(config: OptimizerConfig) -> str:
    return f"""Run the XML optimizer workflow now.

Required workflow:
1. Call dump_full_xml once to create XML0.
2. Call analyze_hidden_subtrees with XML0 and retain the candidate evidence.
3. Call spawn with role="recognizer".
4. Call the recognizer subagent with XML0 to create L0.
5. Repeat optimization rounds until should_stop returns stop=true:
   - call optimize_xml with XML0 to create the next XML result;
   - inspect the optimize_xml response. If ok=false, keep its fixed -1000
     score_ref, include the reported optimizer error in your reasoning, skip
     recognizer and score_round for that failed XML, and propose a corrected
     complete optimizer script;
   - otherwise, call the recognizer subagent with that XML result to create the
     next L result, then call score_round with XML0, that XML result, L0, and
     the latest L result;
   - inspect get_optimizer_source. If hidden subtree candidates were reported,
     prioritize implementing their exact pruning rule before generic compression;
   - call apply_optimizer with score_ref, a concrete reason, and the complete script.
6. Call kill for the recognizer subagent before finishing.

Limits:
- max_rounds={config.max_rounds}
- min_growth={config.min_growth}
- stale_rounds={config.stale_rounds}

Final response should summarize best score, rounds completed, and optimizer path {config.output}.
"""


def _extract_json(raw: str) -> str:
    if raw.startswith("```"):
        raw = _strip_fence(raw)
    if raw.startswith("{"):
        return raw
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else raw


def _strip_fence(raw: str) -> str:
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
