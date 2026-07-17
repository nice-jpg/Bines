import re


def _parse_nodes(xml: str):
    nodes = []
    for m in re.finditer(r'<n\b([^>]*)/>', xml):
        attrs = m.group(1)
        text_m = re.search(r'\bt="([^"]*)"', attrs)
        bound_m = re.search(r'\bb="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', attrs)
        if not bound_m:
            continue
        x1, y1, x2, y2 = map(int, bound_m.groups())
        nodes.append(
            {
                "text": text_m.group(1) if text_m else "",
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "cx": (x1 + x2) / 2,
                "cy": (y1 + y2) / 2,
            }
        )
    return nodes


def _is_rating(text: str) -> bool:
    return bool(re.fullmatch(r'\d(?:\.\d)?', text or ""))


def _is_distance(text: str) -> bool:
    return bool(re.fullmatch(r'\d+(?:\.\d+)?(?:km|m)', text or ""))


def _is_noise(text: str) -> bool:
    if not text:
        return True
    noise_patterns = [
        r'^分$', r'^月售', r'^人均', r'^起送', r'^免配送费$', r'^配送',
        r'^\d+分钟$', r'^美团专送$', r'^商家自配$', r'^堂食店$',
        r'^综合排序$', r'^速度$', r'^全部筛选$', r'^请输入', r'^全部$',
    ]
    return any(re.search(p, text) for p in noise_patterns)


def _extract_name(nodes, rating_node):
    candidates = []
    for n in nodes:
        txt = n["text"]
        if _is_noise(txt) or _is_rating(txt) or _is_distance(txt):
            continue
        if n["y2"] <= rating_node["y1"] + 8 and n["y1"] >= rating_node["y1"] - 140:
            if n["x1"] >= rating_node["x1"] - 30 and n["x1"] > 250:
                candidates.append(n)
    candidates.sort(key=lambda n: (n["y1"], n["x1"]))
    line_texts = []
    seen = set()
    for n in candidates:
        txt = n["text"].strip()
        if not txt or txt in seen:
            continue
        seen.add(txt)
        line_texts.append(txt)
    if not line_texts:
        return ""
    return "".join(line_texts)


def run(xml: str) -> dict:
    try:
        nodes = _parse_nodes(xml)
        text_nodes = [n for n in nodes if n["text"]]
        ratings = [n for n in text_nodes if _is_rating(n["text"])]
        merchants = []
        seen = set()
        for rating in ratings:
            distance = None
            for n in text_nodes:
                if _is_distance(n["text"]) and abs(n["cy"] - rating["cy"]) <= 80 and n["x1"] > rating["x2"]:
                    if distance is None or n["x1"] > distance["x1"]:
                        distance = n
            if not distance:
                continue
            name = _extract_name(text_nodes, rating)
            if not name:
                continue
            key = (name, rating["text"], distance["text"])
            if key in seen:
                continue
            seen.add(key)
            merchants.append(
                {
                    "name": name,
                    "rating": rating["text"],
                    "distance": distance["text"],
                }
            )
        return {"merchants": merchants}
    except Exception:
        return {"merchants": []}
