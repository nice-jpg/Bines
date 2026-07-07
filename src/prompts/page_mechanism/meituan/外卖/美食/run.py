from __future__ import annotations

import re
from typing import Any


def parse_nodes(xml: str) -> list[dict[str, Any]]:
    """Parse the custom XML format into a list of node dicts."""
    nodes = []
    pattern = r'<n\s+([^>]*?)\s*/>'
    for match in re.finditer(pattern, xml):
        attrs_str = match.group(1)
        attrs = {}
        for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*?)"', attrs_str):
            attrs[k] = v
        for k, v in re.findall(r"(\w+)\s*=\s*'([^']*?)'", attrs_str):
            attrs[k] = v
        nodes.append(attrs)
    return nodes


def parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
    """Parse bounds string like '[0,0][1080,2400]' into (x1, y1, x2, y2)."""
    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return None


def try_parse_float(s: str) -> float | None:
    """Try to parse a float from string."""
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def try_parse_distance(s: str) -> float | None:
    """Parse distance like '5.1km', '742m' into float in km."""
    if not s:
        return None
    s = s.strip().lower()
    m = re.match(r'^([\d.]+)\s*km$', s)
    if m:
        return float(m.group(1))
    m = re.match(r'^([\d.]+)\s*m$', s)
    if m:
        return float(m.group(1)) / 1000.0
    return None


def is_merchant_name(text: str) -> bool:
    """Check if text looks like a merchant name (not a label/short tag)."""
    # Merchant names are typically Chinese restaurant names containing characters
    # like 店, 家, etc. or just long meaningful names.
    # Filter out obvious non-names
    skip_labels = {
        '分', '¥', '起送', '配送', '人均', '月售', '进店',
        '今日推荐', '热销', '堂食店', '品质必点', '神券商家',
        '综合排序', '速度', '全部筛选', '全部', '15分钟',
        '饺子馄饨', '汉堡薯条', '意面披萨', '包子粥店', '快餐便当',
        '更多选择按钮', '购物车',
    }
    if text.strip() in skip_labels:
        return False
    if try_parse_float(text) is not None:
        return False
    if try_parse_distance(text) is not None:
        return False
    # Must have at least 3 characters and substantial width (not a tag)
    if len(text) < 3:
        return False
    # Chinese restaurant names usually contain Chinese characters
    # Check if it contains Chinese characters
    has_chinese = bool(re.search(r'[\u4e00-\u9fff]', text))
    if not has_chinese:
        return False
    return True


def run(xml: str, previous: dict | None = None) -> dict:
    nodes = parse_nodes(xml)
    
    # Build a list of text nodes with bounds
    text_nodes = []
    for n in nodes:
        bounds = parse_bounds(n.get('b', ''))
        text = n.get('t', '').strip()
        if bounds and text:
            text_nodes.append({
                'bounds': bounds,
                'text': text,
                'x1': bounds[0],
                'y1': bounds[1],
                'x2': bounds[2],
                'y2': bounds[3],
                'width': bounds[2] - bounds[0],
                'height': bounds[3] - bounds[1],
            })
    
    # Strategy: Find FrameLayout containers that represent merchant cards
    # Merchant cards in the list area are FrameLayout nodes with k="1"
    # They appear in the merchant list section (below the filter bar, y > 1400)
    # For each container, find name, rating, distance inside
    
    # First, find all FrameLayout k=1 containers in the merchant list area
    merchant_containers = []
    for n in nodes:
        c = n.get('c', '')
        k = n.get('k', '')
        if c != 'FrameLayout' or k != '1':
            continue
        bounds = parse_bounds(n.get('b', ''))
        if not bounds:
            continue
        x1, y1, x2, y2 = bounds
        width = x2 - x1
        height = y2 - y1
        
        # Merchant cards in the list area: y > 1400 (below filter), wide enough
        if y1 >= 1400 and width > 400 and height > 100:
            merchant_containers.append(bounds)
    
    # Also look for merchant cards that might not have k="1" but are FrameLayouts
    # Check for the pair pattern (image + info)
    if not merchant_containers:
        for n in nodes:
            c = n.get('c', '')
            if c != 'FrameLayout':
                continue
            bounds = parse_bounds(n.get('b', ''))
            if not bounds:
                continue
            x1, y1, x2, y2 = bounds
            width = x2 - x1
            height = y2 - y1
            # Info container: starts at roughly x=349, wide enough
            if 300 <= x1 <= 400 and y1 >= 1400 and width > 500 and height > 100:
                merchant_containers.append(bounds)
    
    merchants = []
    seen_names = set()
    
    for container_bounds in merchant_containers:
        cx1, cy1, cx2, cy2 = container_bounds
        
        # Find text nodes within this container
        inside = []
        for tn in text_nodes:
            if (tn['x1'] >= cx1 and tn['y1'] >= cy1 and 
                tn['x2'] <= cx2 and tn['y2'] <= cy2):
                inside.append(tn)
        
        if not inside:
            continue
        
        # Sort by y position
        inside.sort(key=lambda x: x['y1'])
        
        name = None
        rating = None
        distance = None
        
        # Find name - first text with Chinese chars, significant width
        for tn in inside:
            if is_merchant_name(tn['text']) and tn['width'] > 100:
                name = tn['text']
                break
        
        if not name:
            continue
        
        # Find rating - a float between 0-10
        for tn in inside:
            pv = try_parse_float(tn['text'])
            if pv is not None and 0 <= pv <= 10:
                rating = pv
                break
        
        if rating is None:
            continue
        
        # Find distance - text like "X.Xkm"
        for tn in inside:
            dv = try_parse_distance(tn['text'])
            if dv is not None:
                distance = dv
                break
        
        if distance is None:
            continue
        
        if name not in seen_names:
            seen_names.add(name)
            merchants.append({
                'name': name,
                'rating': rating,
                'distance': distance
            })
    
    # Merge with previous results from scrolling
    if previous and 'merchants' in previous:
        prev_names = {m['name'] for m in merchants}
        for m in previous['merchants']:
            if m['name'] not in prev_names:
                merchants.append(m)
                prev_names.add(m['name'])
    
    return {'merchants': merchants}
