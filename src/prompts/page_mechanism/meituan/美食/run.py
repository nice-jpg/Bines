import re
import json


def run(xml: str) -> dict:
    """Extract merchant name, rating, distance from Meituan food page."""
    if not xml or not xml.strip():
        return {"merchants": []}

    nodes = _parse_nodes(xml)
    if not nodes:
        return {"merchants": []}

    # Build parent-child hierarchy
    _assign_parents(nodes)

    # Find the inner RecyclerView (the one containing merchant cards at y>=673)
    recycler = None
    for n in nodes:
        if n.get('r') == 'pagecontainer_recyclerview' and n.get('s') == '1':
            b = _parse_bounds(n.get('b', ''))
            if b[1] >= 600:
                recycler = n
                break

    if not recycler:
        return {"merchants": []}

    rb = _parse_bounds(recycler.get('b', ''))

    # Find card candidates: direct children of recycler that are full-width
    # LinearLayouts with height >= 300
    # Deduplicate by bounds string
    seen_bounds = set()
    card_candidates = []
    for n in nodes:
        if n.get('_parent') is not recycler:
            continue
        if n.get('c') != 'LinearLayout':
            continue
        b = n.get('b', '')
        if b in seen_bounds:
            continue
        bounds = _parse_bounds(b)
        if bounds[0] != 0 or bounds[2] != 1080:
            continue
        h = bounds[3] - bounds[1]
        if h < 300:
            continue
        seen_bounds.add(b)
        card_candidates.append(n)

    # Fallback: if no candidates via parent tree, scan by bounds
    if not card_candidates:
        seen_bounds = set()
        for n in nodes:
            if n.get('c') != 'LinearLayout':
                continue
            b = n.get('b', '')
            if b in seen_bounds:
                continue
            bounds = _parse_bounds(b)
            if bounds[0] != 0 or bounds[2] != 1080:
                continue
            h = bounds[3] - bounds[1]
            if not (300 <= h <= 600):
                continue
            if not _is_inside(bounds, rb):
                continue
            seen_bounds.add(b)
            card_candidates.append(n)

    merchants = []
    for card in card_candidates:
        cb = _parse_bounds(card.get('b', ''))
        texts = _collect_texts_in(card, nodes)

        name = ''
        rating = ''
        distance = ''

        for t in texts:
            txt = t['text'].strip()
            if not txt:
                continue

            # Rating: like "4.8"
            if not rating and re.match(r'^[45]\.\d$', txt):
                rating = txt

            # Distance: starts with 距该地
            elif not distance and txt.startswith('距该地'):
                distance = txt

            # Merchant name: Chinese text >= 4 chars, not price/promo/category
            elif not name and _is_merchant_name(txt):
                name = txt

        merchants.append({
            'name': name,
            'rating': rating,
            'distance': distance
        })

    return {"merchants": merchants}


def _is_merchant_name(txt: str) -> bool:
    """Heuristic to filter out non-merchant-name text."""
    if len(txt) < 4:
        return False
    if re.match(r'^[0-9a-zA-Z\s]+$', txt):
        return False
    if txt.startswith('¥') or txt.startswith('距'):
        return False
    if re.match(r'^\d+\.?\d*$', txt):
        return False
    # Common non-merchant suffixes/patterns
    non_name_keywords = [
        '条', '人', '折', '促销', '收录', '消费', '包厢', '服务',
        '人气', '推荐', '毕业', '足球', '先核销', '实惠', '限量',
        '锅香', '正宗', '单人', '双人', '3-4', '半只', '四分之一',
        '养生', '佳品', '招牌', '营养', '八宝粥', '米饭', '烤鸭',
        '卤汁', '小吃', '饮品', '特惠'
    ]
    # Check if text matches a promo/deal pattern
    if any(kw in txt for kw in non_name_keywords):
        # But it might still be a name if it's long enough and has 店/餐厅
        if '店' in txt or '餐厅' in txt or '馆' in txt or '屋' in txt:
            return True
        return False
    return True


def _parse_nodes(xml: str) -> list:
    """Parse flat XML into a list of node dicts."""
    nodes = []
    for match in re.finditer(r'<n\s+(.*?)\s*/>', xml):
        raw = match.group(1)
        attrs = re.findall(r'(\w+)=["\'](.*?)["\']', raw)
        node = {}
        for k, v in attrs:
            node[k] = v
        node['_children'] = []
        nodes.append(node)
    return nodes


def _parse_bounds(b: str) -> tuple:
    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', b)
    if m:
        return (int(m.group(1)), int(m.group(2)),
                int(m.group(3)), int(m.group(4)))
    return (0, 0, 0, 0)


def _is_inside(inner: tuple, outer: tuple) -> bool:
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def _area(b: tuple) -> int:
    return (b[2] - b[0]) * (b[3] - b[1])


def _assign_parents(nodes: list):
    """Assign each node's direct parent based on strict bounds containment."""
    for node in nodes:
        nb = _parse_bounds(node.get('b', ''))
        if nb == (0, 0, 0, 0):
            node['_parent'] = None
            continue
        best = None
        best_area = float('inf')
        for other in nodes:
            if other is node:
                continue
            ob = _parse_bounds(other.get('b', ''))
            if ob == (0, 0, 0, 0):
                continue
            if nb == ob:
                continue  # Skip nodes with identical bounds
            if _is_inside(nb, ob):
                a = _area(ob)
                if a < best_area:
                    best_area = a
                    best = other
        node['_parent'] = best
        if best is not None:
            best['_children'].append(node)


def _collect_texts_in(container: dict, all_nodes: list) -> list:
    """Get all leaf text nodes fully inside container bounds."""
    cb = _parse_bounds(container.get('b', ''))
    results = []
    for n in all_nodes:
        t = n.get('t', '')
        if not t:
            continue
        nb = _parse_bounds(n.get('b', ''))
        if _is_inside(nb, cb):
            results.append({'text': t, 'bounds': nb})
    results.sort(key=lambda x: x['bounds'][1])
    return results
