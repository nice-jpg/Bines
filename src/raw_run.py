from agent import AgentRuntime
from model import build_codex_model
from tools.common import CaptchaAuthenticationTool, OperationNoticeTool, create_common_tools
from agent import AgentRunResult
from collections.abc import Callable


AgentRunLoop = Callable[[], AgentRunResult]
Evaluator = Callable[[AgentRunResult], int]

class PlainNotifier:
    def set(self, _) -> None:
        pass

    def __call__(self, text: str) -> None:
        print(f"[NOTIFY] {text}")


def run() -> None:
    notifier = PlainNotifier()
    operation_notice = OperationNoticeTool(notifier=notifier)
    captcha_authentication = CaptchaAuthenticationTool(notifier=notifier)
    runtime = AgentRuntime(
        model=build_codex_model(),
        tools=create_common_tools(
            operation_notice_tool=operation_notice,
            captcha_authentication_tool=captcha_authentication,
        ),
        name="main",
    )

    r = runtime.run_turn(
        [{"role": "user", "content": "执行应用探测任务"}],
        session_id="main",
        max_iterations=1000,
    )

    print(r)

def learn(run_loop: AgentRunLoop, eval: Evaluator) -> None:
    """Run the agent loop and evaluate the result."""
    result = run_loop()
    score = eval(result)
    print(f"Run result score: {score}")


run()