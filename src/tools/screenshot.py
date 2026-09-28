"""Shared PNG evidence capture for tools and offline collection audits."""

import base64
from pathlib import Path
from tempfile import TemporaryDirectory


def capture_png(hub) -> bytes:
    remote = hub.screenshot()
    if not isinstance(remote, str):
        raise ValueError(f"Screenshot failed: {remote}")
    with TemporaryDirectory(prefix="page-image-") as directory:
        local = str(Path(directory) / "screen.png")
        result = hub.device.pull_file(remote, local)
        if not isinstance(result, str):
            raise ValueError(f"Screenshot transfer failed: {result}")
        data = Path(local).read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Screenshot is not a PNG image")
    return data


def image_url(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def screenshot_content(hub):
    return [{"type": "image_url", "image_url": {"url": image_url(capture_png(hub))}}]
