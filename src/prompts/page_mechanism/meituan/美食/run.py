import re


def run(xml: str) -> dict:
    if not xml or not xml.strip():
        return {"merchants": []}

    nodes = _parse_nodes(xml)
    if not nodes:
        return {"merchants": []}

    recycler_bounds = _find_list_recycler(nodes)
    if not recycler_bounds:
        return {"merchants": []}

    cards = _find_card_candidates(nodes, recycler_bounds)
    merchants = []
    for card in cards:
        texts = _texts_in_bounds(nodes, card)
        merchant = _extract_card(texts, card)
        if merchant["name"] or merchant["rating"] or merchant["distance"]:
            merchants.append(merchant)

    return {"merchants": merchants}


def _extract_card(texts, card_bounds):
    left, top, right, bottom = card_bounds

    rating_node = None
    for item in sorted(texts, key=lambda x: (x["bounds"][1], x["bounds"][0])):
        txt = item["text"].strip()
        if re.fullmatch(r"[0-5]\.\d", txt):
            rating_node = item
            break
    rating = rating_node["text"] if rating_node else ""

    distance_candidates = [
        item for item in texts
        if item["text"].strip().startswith("距该地")
    ]
    distance = ""
    if distance_candidates:
        distance = sorted(distance_candidates, key=lambda x: (x["bounds"][1], -x["bounds"][0]))[0]["text"]

    title_bottom = (rating_node["bounds"][1] - 8) if rating_node else min(top + 140, bottom)
    title_nodes = []
    for item in texts:
        txt = item["text"].strip()
        x1, y1, x2, y2 = item["bounds"]
        if not txt:
            continue
        if x1 < left + 180:
            continue
        if y1 < top or y2 > title_bottom + 5:
            continue
        if _is_bad_title_text(txt):
            continue
        title_nodes.append(item)

    name = _merge_title_nodes(title_nodes)

    return {
        "name": name,
        "rating": rating,
        "distance": distance,
    }


def _merge_title_nodes(nodes):
    if not nodes:
        return ""
    nodes = sorted(nodes, key=lambda x: (x["bounds"][1], x["bounds"][0]))
    rows = []
    for item in nodes:
        x1, y1, x2, y2 = item["bounds"]
        placed = False
        for row in rows:
            if abs(row["y"] - y1) <= 24:
                row["items"].append(item)
                row["y"] = min(row["y"], y1)
                placed = True
                break
        if not placed:
            rows.append({"y": y1, "items": [item]})

    parts = []
    for row in sorted(rows, key=lambda r: r["y"]):
        row_items = sorted(row["items"], key=lambda x: x["bounds"][0])
        parts.append("".join(item["text"].strip() for item in row_items if item["text"].strip()))
    return "".join(parts)


def _is_bad_title_text(txt):
    if not txt:
        return True
    if txt.startswith("距该地") or txt.startswith("¥"):
        return True
    if re.fullmatch(r"[0-5]\.\d", txt):
        return True
    if re.search(r"\d+条$", txt):
        return True
    if re.search(r"\d+/人$", txt):
        return True
    if any(k in txt for k in ["营业", "广告", "会员价", "美团补贴", "收录", "消费人数", "人气榜", "夜宵榜", "第", "折"]):
        return True
    if len(txt) <= 1:
        return True
    return False


def _find_list_recycler(nodes):
    candidates = []
    for n in nodes:
        if n.get("c") == "RecyclerView" and n.get("r") == "pagecontainer_recyclerview":
            b = _parse_bounds(n.get("b", ""))
            if b[1] >= 600:
                candidates.append(b)
    if not candidates:
        return None
    candidates.sort(key=lambda b: (b[1], -(b[3] - b[1])))
    return candidates[0]


def _find_card_candidates(nodes, recycler_bounds):
    results = []
    seen = set()
    rx1, ry1, rx2, ry2 = recycler_bounds
    for n in nodes:
        if n.get("c") != "ViewGroup":
            continue
        b = _parse_bounds(n.get("b", ""))
        x1, y1, x2, y2 = b
        if (x2 - x1) < 900:
            continue
        if x1 > 40 or x2 < 1040:
            continue
        if (y2 - y1) < 250:
            continue
        if y1 < ry1 or y2 > ry2:
            continue
        if b in seen:
            continue
        texts = _texts_in_bounds(nodes, b)
        if not any(re.fullmatch(r"[0-5]\.\d", t["text"].strip()) for t in texts):
            continue
        if not any(t["text"].strip().startswith("距该地") for t in texts):
            continue
        seen.add(b)
        results.append(b)
    results.sort(key=lambda b: (b[1], b[0]))
    return results


def _texts_in_bounds(nodes, bounds):
    results = []
    for n in nodes:
        txt = n.get("t", "")
        if not txt:
            continue
        nb = _parse_bounds(n.get("b", ""))
        if _inside(nb, bounds):
            results.append({"text": txt, "bounds": nb})
    return results


def _parse_nodes(xml):
    nodes = []
    for m in re.finditer(r'<n\s+(.*?)\/>', xml):
        raw = m.group(1)
        attrs = dict(re.findall(r'(\w+)=["\'](.*?)["\']', raw))
        nodes.append(attrs)
    return nodes


def _parse_bounds(s):
    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', s or '')
    if not m:
        return (0, 0, 0, 0)
    return tuple(int(m.group(i)) for i in range(1, 5))


def _inside(inner, outer):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]
