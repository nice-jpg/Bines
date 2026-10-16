"""PNG-to-model adapter and legacy exception boundary."""

import base64
from typing import Protocol

from src.device import DeviceError
from src.device import ImageContent as ImageContent
from src.device import ImageUrl as ImageUrl


class ScreenshotSource(Protocol):
    def capture_png(self) -> bytes | DeviceError: ...


def capture_png(hub: ScreenshotSource) -> bytes:
    result: bytes | DeviceError = hub.capture_png()
    if isinstance(result, DeviceError):
        raise ValueError(f"Screenshot failed: {result.message}")
    return result


def image_url(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def screenshot_content(hub: ScreenshotSource) -> list[ImageContent]:
    return [{"type": "image_url", "image_url": {"url": image_url(capture_png(hub))}}]
