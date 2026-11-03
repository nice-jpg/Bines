"""Adapt ``src/application/raw_run.py`` to the mind_controller slave contract."""

from __future__ import annotations

import asyncio
import importlib
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import current_thread, main_thread
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent_hypervisor.host import Host
from src.agent_runtime.types.execution import EngineResult, EngineStatus, InterruptKind
from src.channel.messages import InBoundMessage
from src.common.tracing import trace_client
from src.conductor.engine import ConductorEngine
from src.configuration import get_model_instance
from src.configuration.manager import ConfigurationManager
from src.configuration.models import close_model
from src.prompts import conductor_prompt

try:
    # This is the canonical import when launched through mind_controller/run_master.py.
    from mind_controller_agent import SlaveDebugInfo
except ModuleNotFoundError:
    # Also support importing trainer directly from the repository root.
    from mind_controller.mind_controller_agent import SlaveDebugInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SYSTEM_PROMPT_PATH = SRC_DIR / "prompts" / "conductor_prompt.py"
PAGE_MECHANISM_DIR = SRC_DIR / "prompts" / "page_mechanism"
APP_PROBE_MESSAGE = "执行应用探测任务"
CAPTCHA_RESUME_TEXT = "done"

# raw_run.py intentionally uses top-level imports so it can be launched as a
# script. Make that same import mode available to this package adapter.
src_entry = str(SRC_DIR)
if src_entry not in sys.path:
    sys.path.insert(0, src_entry)


@dataclass
class RunResult:
    output: str
    state: Mapping[str, object]
    summary: list[dict[str, object]] = field(default_factory=list)


class TrainingEngine(ConductorEngine):
    def _system_prompt(self) -> str:
        return conductor_prompt.CONDUCTOR_PROMPT

    @property
    def evaluation_state(self) -> Mapping[str, object]:
        # A training report is read after execution; Coordinator never reads graph state.
        return dict(self._state)


Evaluator = Callable[[RunResult], int | dict[str, Any]]
EngineFactory = Callable[..., TrainingEngine]
ModelFactory = Callable[[], Any]
NotifierFactory = Callable[[], Callable[[str], None]]
PromptReloader = Callable[[], EngineFactory]
HumanInputProvider = Callable[[EngineResult], str]


class RawRunSlave:
    """Run the app-probe workflow and expose it as a cognitive slave."""

    def __init__(
        self,
        *,
        evaluator: Evaluator | None = None,
        engine_factory: EngineFactory | None = None,
        model_factory: ModelFactory | None = None,
        notifier_factory: NotifierFactory | None = None,
        prompt_reloader: PromptReloader | None = None,
        human_input_provider: HumanInputProvider | None = None,
        task_message: str = APP_PROBE_MESSAGE,
        session_id: str = "main",
        max_iterations: int = 1000,
        print_result: bool = True,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self._evaluator = evaluator or _evaluate_raw_run
        self._engine_factory = engine_factory
        self._model_factory = model_factory or _build_raw_model
        self._notifier_factory = notifier_factory or _build_plain_notifier
        self._prompt_reloader = prompt_reloader or _reload_training_engine
        self._human_input_provider = human_input_provider or _read_human_input
        self.task_message = task_message
        self.session_id = session_id
        self.max_iterations = max_iterations
        self.print_result = print_result

    def run(self) -> RunResult:
        """Return the complete raw result with an LLM-safe summary attached."""

        if current_thread() is main_thread():
            ConfigurationManager.initialize()
        else:
            ConfigurationManager.instance()
        trace_client()
        factory = self._engine_factory or self._prompt_reloader()
        notifier = self._notifier_factory()
        host = Host()
        parent_id = host.router.register_root()
        host.router.deliver_user(
            InBoundMessage("training", self.session_id, uuid4().hex, self.task_message, "trainer")
        )
        host.router.read(parent_id, wait=False)
        model = None
        try:
            model = self._model_factory()
            with host.child(parent_id) as handle:
                engine = factory(model=model, handle=handle, max_iterations=self.max_iterations)
                host.start(engine)
            child_id = handle.agent_id
            host.router.call(parent_id, child_id, "应用探测", self.task_message)
            output = ""
            finished = False
            while not finished:
                messages = host.router.read(parent_id, wait=True)
                host.router.acknowledge(parent_id, tuple(message.message_id for message in messages))
                for message in messages:
                    if message.result is None:
                        continue
                    notifier(message.result.summary)
                    if message.result.status in {"completed", "failed"}:
                        output = message.result.summary
                        host.router.accept_report(parent_id, message.task_id)
                        finished = True
                    elif message.result.status == "blocked":
                        raw_kind = message.result.data.get("interrupt_kind")
                        kind = (
                            InterruptKind(raw_kind) if isinstance(raw_kind, str) else InterruptKind.EXECUTION
                        )
                        prompt = EngineResult(EngineStatus.INTERRUPTED, message.result.summary, kind)
                        response = self._human_input_provider(prompt)
                        if raw_kind is not None:
                            token = "done" if kind == InterruptKind.HUMAN else "继续"
                            while response.strip() != token:
                                response = self._human_input_provider(prompt)
                        host.router.call(parent_id, child_id, "应用探测", response)
            host.kill(parent_id, child_id)
            report = RunResult(output, engine.evaluation_state)
            report.summary = self._summary(report)
            return report
        finally:
            try:
                asyncio.run(host.close())
            finally:
                host.router.release(parent_id)
                if model is not None:
                    close_model(model)

    def eval(self, result: RunResult) -> int | dict[str, Any]:
        """Delegate scoring to the evaluator defined by raw_run."""

        return self._evaluator(result)

    def debug_info(self) -> SlaveDebugInfo:
        """Describe the prompt surface that may be optimized by the master."""

        prompt_paths = (
            SYSTEM_PROMPT_PATH,
            *sorted(PAGE_MECHANISM_DIR.rglob("PAGE.md")),
        )
        return SlaveDebugInfo(
            prompt_paths=prompt_paths,
            prompt_structure=(
                "conductor_prompt.py defines the global collection policy and tool protocol. "
                "Each PAGE.md defines canonical, page-specific operating instructions and "
                "is injected by the runtime together with task.md for the current page path."
            ),
            responsibility=(
                "Run one complete Android application-probing and merchant/product "
                "collection task for the workspace configuration."
            ),
            expected_outcome=(
                "Complete the configured collection accurately, preserve required merchant "
                "and product fields, follow operation-notice/manual/captcha protocols, and "
                "write valid grouped rows to the workspace Excel output."
            ),
            additional_context={
                "entrypoint": str(SRC_DIR / "raw_run.py"),
                "task_message": self.task_message,
                "session_id": self.session_id,
                "max_iterations": self.max_iterations,
                "evaluator": (
                    f"{getattr(self._evaluator, '__module__', type(self._evaluator).__module__)}."
                    f"{getattr(self._evaluator, '__name__', type(self._evaluator).__name__)}"
                ),
                "prompt_reload": (
                    "The adapter reloads the global system prompt before constructing every "
                    "runtime. PAGE.md files are read from disk by query_manual."
                ),
                "hitl_resume": (
                    "If the run is interrupted for captcha authentication, the adapter keeps "
                    "the same runtime and session alive, waits for exact input 'done', and "
                    "calls resume_turn until the task completes."
                ),
            },
            working_directory=REPO_ROOT,
        )

    def _summary(self, result: RunResult) -> list[Any]:
        state = result.state
        if not isinstance(state, Mapping):
            raise ValueError("raw run result.state must be a mapping")
        messages = state.get("messages")
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            raise ValueError("raw run result.state['messages'] must be a sequence")

        reports: list[Any] = []
        for message in messages:
            if isinstance(message, HumanMessage):
                reports.append(
                    {
                        "type": message.type,
                        "content": message.content,
                    }
                )
            elif isinstance(message, AIMessage):
                reports.append(
                    {
                        "type": message.type,
                        "content": message.content,
                        "tool_calls": [
                            {
                                "name": tool_call["name"],
                                "args": tool_call["args"],
                            }
                            for tool_call in message.tool_calls
                        ],
                    }
                )
            elif isinstance(message, ToolMessage):
                if message.name == "notify_user":
                    continue
                reports.append(
                    {
                        "type": message.type,
                        "name": message.name,
                        "status": message.status,
                    }
                )
        return reports


def _evaluate_raw_run(result: RunResult) -> int | dict[str, Any]:
    return _raw_run_module().eval(result)


def _build_raw_model() -> Any:
    return get_model_instance("behave")


def _build_plain_notifier() -> Callable[[str], None]:
    return _raw_run_module().PlainNotifier()


def _raw_run_module() -> Any:
    return importlib.import_module("src.application.raw_run")


def _reload_training_engine() -> EngineFactory:
    """Reload the prompt read by the training engine's protected extension point."""
    importlib.invalidate_caches()
    importlib.reload(conductor_prompt)
    return TrainingEngine


def _read_human_input(result: EngineResult) -> str:
    token = CAPTCHA_RESUME_TEXT if result.interrupt_kind == InterruptKind.HUMAN else "继续"
    try:
        return input(f"Agent execution paused. Resolve the issue, then enter {token!r}: ")
    except EOFError as exc:
        raise RuntimeError(
            "HITL input is unavailable; provide RawRunSlave(human_input_provider=...) "
            "for non-interactive execution"
        ) from exc


# Importable instance for:
# python mind_controller/run_master.py --slave trainer.raw_run_adapter:slave
slave = RawRunSlave()
