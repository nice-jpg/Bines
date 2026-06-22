import re
import xml.etree.ElementTree as ET

# Attrs the recognizer needs
TEXT_KEYS = ("text", "content-desc")
# Remove resource-id entirely - recognizer doesn't need it
EXTRA_KEYS = ("class",)
# Booleans we keep (when "true")
BOOL_KEYS = ("clickable", "enabled", "focusable", "long-clickable")

def optimize(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return re.sub(r">\s+<", "><", xml_text).strip()

    groups = {}
    for elem in root.iter():
        if elem.tag == "hierarchy":
            continue
        attrs = elem.attrib
        bounds = attrs.get("bounds", "").strip()
        if not _positive_bounds(bounds):
            continue
        node = {"bounds": bounds}
        for key in TEXT_KEYS:
            val = attrs.get(key, "").strip()
            if val:
                node[key] = re.sub(r"\s+", " ", val)
        for key in EXTRA_KEYS:
            val = attrs.get(key, "").strip()
            if val:
                node[key] = val
        for key in BOOL_KEYS:
            if attrs.get(key) == "true":
                node[key] = "true"
        groups.setdefault(bounds, []).append(node)

    merged = []
    for b, group in groups.items():
        m = {"bounds": b}
        for key in TEXT_KEYS + EXTRA_KEYS:
            vals = [n.get(key) for n in group if n.get(key)]
            if vals:
                m[key] = vals[0]
        for key in BOOL_KEYS:
            if any(key in n for n in group):
                m[key] = "true"
        merged.append(m)

    # Minimal header
    header = "<h>"
    body = "".join("<n" + _attrs(m.items()) + "/>" for m in merged)
    return header + body + "</h>"

def _attrs(items) -> str:
    parts = []
    for key, value in items:
        if value is None or value == "":
            continue
        escaped = str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(f' {key}="{escaped}"')
    return "".join(parts)

def _positive_bounds(value: str) -> bool:
    match = re.match(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$", value)
    if not match:
        return False
    left, top, right, bottom = (int(part) for part in match.groups())
    return right > left and bottom > top
