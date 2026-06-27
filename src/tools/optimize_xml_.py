import re
import xml.etree.ElementTree as ET

SPACE_RE = re.compile(r"\s+")
BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")
CLASS_PREFIXES = (
    "android.widget.", "android.view.", "android.app.", "android.webkit.",
    "androidx.recyclerview.widget.", "androidx.viewpager.widget.",
    "android.support.v7.widget.", "android.support.v4.view.",
)
KEEP_CLASS = {
    "EditText", "ImageButton", "CheckBox", "RadioButton", "Switch",
    "CheckedTextView", "SeekBar", "Spinner", "TabWidget", "WebView",
    "RecyclerView", "ViewPager", "ListView", "GridView", "HorizontalScrollView",
    "ScrollView"
}
DROP_CLASS_ON_LABELED = {"TextView", "ImageView", "View", "Button", "ImageButton"}
GENERIC_RIDS = {
    "root", "content", "container", "layout", "view", "group", "wrapper", "item",
    "card", "cell", "icon", "image", "img", "text", "title", "button", "btn", "tab", "bar",
    "bottom", "layer", "guide", "home", "mine", "message", "cart", "search", "locate",
    "status", "panel", "parent", "child", "holder", "frame", "main", "line", "bg"
}


def optimize(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return re.sub(r">\s+<", "><", xml_text).strip()

    out = ["<h>"]
    seen = set()
    for elem in root.iter():
        if elem.tag == "hierarchy":
            continue
        rec = _make_record(elem.attrib)
        if rec is None:
            continue
        sig = tuple(rec)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(_emit(rec))
    out.append("</h>")
    return "".join(out)


def _make_record(attrs):
    bounds = attrs.get("bounds", "")
    if not _positive_bounds(bounds):
        return None
    text = _norm(attrs.get("text", ""))
    desc = _norm(attrs.get("content-desc", ""))
    if desc == text:
        desc = ""
    cls = _short_class(attrs.get("class", ""))
    rid = _rid_tail(attrs.get("resource-id", ""))
    clickable = attrs.get("clickable") == "true"
    scrollable = attrs.get("scrollable") == "true"
    selected = attrs.get("selected") == "true"
    checked = attrs.get("checked") == "true"

    has_label = bool(text or desc)
    if not (has_label or clickable or scrollable or selected or checked or cls in KEEP_CLASS):
        return None

    rec = [bounds]
    if text:
        rec.append(("t", text))
    if desc:
        rec.append(("d", desc))
    if cls and (cls in KEEP_CLASS or (clickable and not has_label) or scrollable) and not (has_label and cls in DROP_CLASS_ON_LABELED):
        rec.append(("c", cls))
    if rid and (not has_label or clickable or selected or checked) and _meaningful_rid(rid):
        rec.append(("r", rid))
    if clickable:
        rec.append(("k", "1"))
    if scrollable:
        rec.append(("s", "1"))
    if selected:
        rec.append(("sel", "1"))
    if checked:
        rec.append(("chk", "1"))
    return rec


def _emit(rec):
    out = ["<n b=\"", _esc(rec[0]), "\""]
    for k, v in rec[1:]:
        out += [" ", k, "=\"", _esc(v), "\""]
    out.append("/>")
    return "".join(out)


def _norm(v):
    return SPACE_RE.sub(" ", v or "").strip()


def _short_class(v):
    v = (v or "").strip()
    for p in CLASS_PREFIXES:
        if v.startswith(p):
            return v[len(p):]
    return v


def _rid_tail(v):
    v = (v or "").strip()
    if not v:
        return ""
    return re.sub(r"[^a-zA-Z0-9_]+", "_", v.split("/")[-1]).strip("_")[:20]


def _meaningful_rid(rid):
    parts = [p.lower() for p in rid.split("_") if p]
    useful = [p for p in parts if len(p) > 2 and p not in GENERIC_RIDS and not p.isdigit()]
    return bool(useful) and len(parts) <= 3


def _esc(s):
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _positive_bounds(v):
    m = BOUNDS_RE.match(v or "")
    return bool(m) and (lambda l, t, r, b: r > l and b > t)(*map(int, m.groups()))
