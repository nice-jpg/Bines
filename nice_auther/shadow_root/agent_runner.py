"""Local Android agent runner for the remote-gateway shadow_root architecture."""

from __future__ import annotations

import time

from .adb import AdbClient
from .android_agent import AndroidShadowAgent
from .config import ShadowConfig
from .process_log import log_event


def start_android_agent(config: ShadowConfig | None = None) -> None:
    effective_config = config or ShadowConfig.from_env()
    adb = AdbClient(effective_config)
    agent = AndroidShadowAgent(adb, effective_config)
    log_event(
        "shadow_root.agent_runner",
        "start",
        transport=effective_config.webrtc_transport,
        rtp_host=effective_config.webrtc_rtp_host,
        rtp_port=effective_config.webrtc_rtp_port,
        gateway_host=effective_config.webrtc_gateway_host,
        gateway_port=effective_config.webrtc_gateway_port,
    )
    agent.start()
    try:
        while True:
            process = agent.process
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"Android agent exited with code {process.returncode}")
            time.sleep(1)
    except KeyboardInterrupt:
        log_event("shadow_root.agent_runner", "interrupted")
    finally:
        agent.stop()
