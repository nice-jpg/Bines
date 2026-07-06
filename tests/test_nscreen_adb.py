from __future__ import annotations

import subprocess
from typing import Any

from nScreen.shadow_root.adb import AdbClient
from nScreen.shadow_root.config import ShadowConfig


def test_android_agent_does_not_inherit_controller_stdin() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    process = object()

    def popen_factory(args: list[str], **kwargs: Any) -> object:
        calls.append((args, kwargs))
        return process

    client = AdbClient(ShadowConfig(adb_path="adb"), popen_factory=popen_factory)

    result = client.start_android_agent(
        "/data/local/tmp/agent.jar",
        "example.AgentMain",
        ["--fps", "30"],
    )

    assert result is process
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
