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
    "ScrollView", "LinearLayout"
}
DROP_CLASS_ON_LABELED = {"TextView", "ImageView", "View", "Button", "ImageButton"}
GENERIC_RIDS = {
    "root", "content", "container", "layout", "view", "group", "wrapper", "item",
    "card", "cell", "icon", "image", "img", "text", "title", "button", "btn", "tab", "bar",
    "bottom", "layer", "guide", "home", "mine", "message", "cart", "search", "locate",
    "status", "panel", "parent", "child", "holder", "frame", "main", "line", "bg"
}
PULL_HINTS = ("pull_loading", "pull-loading", "loading_bg", "loadingbg", "preload")


def optimize(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return re.sub(r">\s+<", "><", xml_text).strip()

    _prune_hidden(root)

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


def _prune_hidden(root):
    for parent in list(root.iter()):
        siblings = list(parent)
        for child in siblings:
            if _should_prune(child, parent, siblings):
                parent.remove(child)


def _should_prune(elem, parent, siblings):
    vis = (elem.attrib.get("visible-to-user") or "").strip().lower()
    if vis == "false":
        return True
    return _is_legacy_pull_loading_candidate(elem, parent, siblings)


def _is_legacy_pull_loading_candidate(elem, parent, siblings):
    rid_full = (elem.attrib.get("resource-id") or "").strip().lower()
    rid_tail = _rid_tail(rid_full).lower()
    joined = rid_full + " " + rid_tail
    if not any(h in joined for h in PULL_HINTS):
        return False
    eb = _parse_bounds(elem.attrib.get("bounds", ""))
    pb = _parse_bounds(parent.attrib.get("bounds", ""))
    if not eb or not pb or _area_ratio(eb, pb) < 0.95:
        return False
    if _actionable_descendants(elem) != 0:
        return False
    return any(
        sib is not elem and (sb := _parse_bounds(sib.attrib.get("bounds", ""))) and _overlap_area(eb, sb) > 0 and _actionable_descendants(sib) >= 2
        for sib in siblings
    )


def _actionable_descendants(elem):
    return sum(
        1 for d in elem.iter()
        if d.attrib.get("clickable") == "true" or d.attrib.get("long-clickable") == "true" or d.attrib.get("scrollable") == "true"
    )


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
    if rid and (clickable or selected or checked or not has_label) and _meaningful_rid(rid):
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
    return "<n b=\"" + _esc(rec[0]) + "\"" + "".join(f' {k}=\"{_esc(v)}\"' for k, v in rec[1:]) + "/>"


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
    return re.sub(r"[^a-zA-Z0-9_]+", "_", v.split("/")[-1]).strip("_")[:40] if v else ""


def _meaningful_rid(rid):
    parts = [p.lower() for p in rid.split("_") if p]
    useful = [p for p in parts if len(p) > 2 and p not in GENERIC_RIDS and not p.isdigit()]
    return bool(useful) and len(parts) <= 4


def _esc(s):
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _positive_bounds(v):
    m = BOUNDS_RE.match(v or "")
    return bool(m) and (lambda l, t, r, b: r > l and b > t)(*map(int, m.groups()))


def _parse_bounds(v):
    m = BOUNDS_RE.match(v or "")
    if not m:
        return None
    l, t, r, b = map(int, m.groups())
    return (l, t, r, b) if r > l and b > t else None


def _area_ratio(inner, outer):
    ia = (inner[2] - inner[0]) * (inner[3] - inner[1])
    oa = (outer[2] - outer[0]) * (outer[3] - outer[1])
    return ia / oa if oa > 0 else 0.0


def _overlap_area(a, b):
    l = max(a[0], b[0])
    t = max(a[1], b[1])
    r = min(a[2], b[2])
    bb = min(a[3], b[3])
    return max(0, r - l) * max(0, bb - t)
