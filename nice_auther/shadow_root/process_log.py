"""Process stdout logging helpers for shadow_root subprocesses."""

from __future__ import annotations

import json
import subprocess
import threading
from typing import Any


def log_event(component: str, message: str, **fields: Any) -> None:
    payload = {"component": component, "message": message, **fields}
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def drain_process_output(component: str, process: subprocess.Popen[str] | None) -> None:
    if process is None or process.stdout is None:
        return
    thread = threading.Thread(target=_drain, args=(component, process), daemon=True)
    thread.start()


def _drain(component: str, process: subprocess.Popen[str]) -> None:
    try:
        assert process.stdout is not None
        for line in process.stdout:
            log_event(component, "stdout", pid=getattr(process, "pid", None), line=line.rstrip("\n"))
    except Exception as exc:
        log_event(component, "stdout drain error", pid=getattr(process, "pid", None), error=str(exc))
