"""Image blocks exposed by device Agent adapters."""

from typing import Literal, TypedDict


class ImageUrl(TypedDict):
    url: str


class ImageContent(TypedDict):
    type: Literal["image_url"]
    image_url: ImageUrl
