"""Map a rooted local Android screen to a temporary remote-control HTTP UI."""

from .adb import AdbClient
from .config import DEFAULT_INPUT_DEVICE, SCHEMA_VERSION, ShadowConfig
from .display import DisplayStreamer, MjpegScreencapStreamer
from .input_stream import InputCapabilities, InputStreamInjector, TouchEventEncoder
from .server import ShadowHTTPServer, start_shadow_session
from .session import ShadowSession

__all__ = [
    "AdbClient",
    "DEFAULT_INPUT_DEVICE",
    "DisplayStreamer",
    "InputCapabilities",
    "InputStreamInjector",
    "MjpegScreencapStreamer",
    "SCHEMA_VERSION",
    "ShadowConfig",
    "ShadowHTTPServer",
    "ShadowSession",
    "TouchEventEncoder",
    "start_shadow_session",
]
