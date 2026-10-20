"""Validated nScreen lifecycle data."""

from typing import NotRequired

from typing_extensions import TypedDict


class ShadowParameters(TypedDict):
    max_size: int
    fps: int
    bitrate: int
    i_frame_interval_ms: int
    rtp_host: str
    rtp_port: int
    mtu: int


class ShadowAgentStatus(TypedDict):
    enabled: bool
    remote_path: str
    main_class: str
    pid: int | None
    returncode: int | None
    running: bool
    params: ShadowParameters


class ShadowServiceStatus(TypedDict):
    ok: bool
    running: bool
    agent: NotRequired[ShadowAgentStatus]
