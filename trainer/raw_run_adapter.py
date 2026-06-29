"""Adapt ``src/raw_run.py`` to the mind_controller slave contract."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

try:
    # This is the canonical import when launched through mind_controller/run_master.py.
    from mind_controller_agent import SlaveDebugInfo
except ModuleNotFoundError:
    # Also support importing trainer directly from the repository root.
    from mind_controller.mind_controller_agent import SlaveDebugInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SYSTEM_PROMPT_PATH = SRC_DIR / "prompts" / "system_prompt.py"
PAGE_MECHANISM_DIR = SRC_DIR / "prompts" / "page_mechanism"
APP_PROBE_MESSAGE = "执行应用探测任务"

# raw_run.py intentionally uses top-level imports so it can be launched as a
# script. Make that same import mode available to this package adapter.
src_entry = str(SRC_DIR)
if src_entry not in sys.path:
    sys.path.insert(0, src_entry)

raw_run = importlib.import_module("raw_run")

RunResult = Any
Evaluator = Callable[[RunResult], int | dict[str, Any]]
RuntimeFactory = Callable[..., Any]
ModelFactory = Callable[[], Any]
ToolFactory = Callable[[Callable[[str], None]], Sequence[Any]]
NotifierFactory = Callable[[], Callable[[str], None]]
PromptReloader = Callable[[], RuntimeFactory]


class RawRunSlave:
    """Run the app-probe workflow and expose it as a cognitive slave."""

    def __init__(
        self,
        *,
        evaluator: Evaluator | None = None,
        runtime_factory: RuntimeFactory | None = None,
        model_factory: ModelFactory | None = None,
        tool_factory: ToolFactory | None = None,
        notifier_factory: NotifierFactory | None = None,
        prompt_reloader: PromptReloader | None = None,
        task_message: str = APP_PROBE_MESSAGE,
        session_id: str = "main",
        max_iterations: int = 1000,
        print_result: bool = True,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self._evaluator = evaluator or raw_run.eval
        self._runtime_factory = runtime_factory
        self._model_factory = model_factory or raw_run.build_codex_model
        self._tool_factory = tool_factory or _build_common_tools
        self._notifier_factory = notifier_factory or raw_run.PlainNotifier
        self._prompt_reloader = prompt_reloader or _reload_agent_runtime
        self.task_message = task_message
        self.session_id = session_id
        self.max_iterations = max_iterations
        self.print_result = print_result

    def run(self) -> RunResult:
        """Execute one complete raw_run task and return its actual result."""

        runtime_factory = self._runtime_factory or self._prompt_reloader()
        notifier = self._notifier_factory()
        runtime = runtime_factory(
            model=self._model_factory(),
            tools=list(self._tool_factory(notifier)),
            name="main",
        )
        result = runtime.run_turn(
            [{"role": "user", "content": self.task_message}],
            session_id=self.session_id,
            max_iterations=self.max_iterations,
        )
        if self.print_result:
            print(result)
        return result

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
                "system_prompt.py defines the global collection policy and tool protocol. "
                "Each PAGE.md defines canonical, page-specific operating instructions and "
                "is loaded dynamically by query_manual using the current page path."
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
            },
            working_directory=REPO_ROOT,
        )


def _build_common_tools(notifier: Callable[[str], None]) -> Sequence[Any]:
    operation_notice = raw_run.OperationNoticeTool(notifier=notifier)
    captcha_authentication = raw_run.CaptchaAuthenticationTool(notifier=notifier)
    return raw_run.create_common_tools(
        operation_notice_tool=operation_notice,
        captcha_authentication_tool=captcha_authentication,
    )


def _reload_agent_runtime() -> RuntimeFactory:
    """Reload the import-time SYSTEM_PROMPT binding used by AgentRuntime."""

    system_prompt_module = importlib.import_module("prompts.system_prompt")
    prompts_module = importlib.import_module("prompts")
    agent_module = importlib.import_module("agent")

    importlib.invalidate_caches()
    importlib.reload(system_prompt_module)
    # prompts.__init__ re-exports SYSTEM_PROMPT, and agent imports that value.
    importlib.reload(prompts_module)
    importlib.reload(agent_module)
    return agent_module.AgentRuntime


# Importable instance for:
# python mind_controller/run_master.py --slave trainer.raw_run_adapter:slave
slave = RawRunSlave()
