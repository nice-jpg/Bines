"""Nonblocking Feishu ingress with one device-owning agent worker."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
from threading import RLock

from src.agent_runtime import RuntimeCommunication
from src.channel.feishu.messages import MessageTarget

logger = logging.getLogger(__name__)


@dataclass
class _Session:
    proxy: RuntimeCommunication
    target: MessageTarget
    running: bool = False
    generation: int = 0


class FeishuAgentRunner:
    def __init__(self, runtime, channel, max_iterations):
        self.runtime = runtime
        self.channel = channel
        self.max_iterations = max_iterations
        self._lock = RLock()
        self._sessions = {}
        self._stopping = False
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-worker")

    def on_message(self, message):
        session_id = f"feishu:{message.chat_id}"
        with self._lock:
            if self._stopping:
                return
            session = self._sessions.get(session_id)
            if session is None:
                target = message.target

                def sender(text):
                    self.channel.send_text(target, text)
                    return True

                session = _Session(self.runtime.bind_communication(session_id, sender), target)
                self._sessions[session_id] = session
            if session.running:
                session.proxy.receive(message.text)
                return
            session.running = True
            session.generation += 1
            self._worker.submit(self._run, session_id, session, message.text, session.generation)

    def _run(self, session_id, session, text, generation):
        try:
            if self.runtime.has_pending_interrupt(session_id):
                kind = self.runtime.pending_interrupt_kind(session_id)
                expected = "继续" if kind in {"model_transport", "execution", "liveness", "recursion"} else "done"
                if text != expected:
                    session.proxy.receive(text)
                    session.proxy.talk(f"补充信息已保存。当前任务等待恢复，请回复“{expected}”继续。")
                    return
                result = self.runtime.resume_turn(session_id=session_id, user_input=text,
                                                  max_iterations=self.max_iterations)
            else:
                result = self.runtime.run_turn([{"role": "user", "content": text}],
                                              session_id=session_id, max_iterations=self.max_iterations)
            while True:
                with self._lock:
                    again = (not result.interrupted and not self._stopping
                             and not session.proxy._close_if_idle())
                    if not again:
                        # Ingress and completion use the same lock. A later arrival starts a new turn.
                        session.running = False
                        if not result.interrupted:
                            self._sessions.pop(session_id, None)
                        break
                result = self.runtime.run_turn([], session_id=session_id, max_iterations=self.max_iterations)
            if result.output:
                self.channel.send_text(session.target, result.output)
        except Exception:
            logger.exception("Feishu agent worker failed for %s", session_id)
            session.proxy.talk("任务运行异常；上下文已保留，请检查日志后继续。")
        finally:
            with self._lock:
                if session.generation == generation:
                    session.running = False

    def close(self):
        with self._lock:
            self._stopping = True
            self.runtime.close()
        self._worker.shutdown(wait=True, cancel_futures=True)
