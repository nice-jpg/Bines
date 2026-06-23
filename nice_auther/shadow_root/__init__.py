"""Map a rooted local Android screen to a temporary remote-control HTTP UI."""

from .adb import AdbClient
from .android_agent import AndroidShadowAgent
from .config import DEFAULT_INPUT_DEVICE, SCHEMA_VERSION, ShadowConfig
from .display import DisplayStreamer, MjpegScreencapStreamer, WebRtcH264Streamer
from .input_stream import InputCapabilities, InputStreamInjector, TouchEventEncoder
from .server import ShadowHTTPServer, start_shadow_session
from .session import ShadowSession
from .tunnel import SshReverseTunnel, tunnel_access_url
from .webrtc import WebRtcGateway

__all__ = [
    "AdbClient",
    "AndroidShadowAgent",
    "DEFAULT_INPUT_DEVICE",
    "DisplayStreamer",
    "InputCapabilities",
    "InputStreamInjector",
    "MjpegScreencapStreamer",
    "SCHEMA_VERSION",
    "ShadowConfig",
    "ShadowHTTPServer",
    "ShadowSession",
    "SshReverseTunnel",
    "TouchEventEncoder",
    "WebRtcGateway",
    "WebRtcH264Streamer",
    "start_shadow_session",
    "tunnel_access_url",
]
