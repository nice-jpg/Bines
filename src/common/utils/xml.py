"""XML text escaping shared by interaction tools."""

from xml.sax.saxutils import escape


def escape_text(value: str) -> str:
    return escape(value)


def escape_attr(value: str) -> str:
    return escape_text(value).replace('"', "&quot;")
