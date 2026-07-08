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
        nodes.append(attrs)
    return nodes


def parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
    """Parse bounds string like '[0,0][1080,2400]' into (x1, y1, x2, y2)."""
    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return None


def is_rating_text(text: str) -> bool:
    """Check if text is a rating value like '4.7', '4.5' etc."""
    if not text:
        return False
    return bool(re.match(r'^[45]\.\d$', text.strip()))


def is_distance_text(text: str) -> bool:
    """Check if text is a distance string e.g., '1.2km', '945m', '7.3km'."""
    if not text:
        return False
    text = text.strip()
    # Match patterns like 1.2km, 945m, 7.3km
    if re.match(r'^[\d.]+(?:km|m)$', text):
        return True
    # Also match numbers followed by km/m that might have slight whitespace
    if re.match(r'^[\d.]+\s*(?:km|m)$', text):
        return True
    return False


def run(xml: str) -> dict:
    nodes = parse_nodes(xml)
    
    # Extract all text nodes with bounds
    text_items = []
    for n in nodes:
        bounds = parse_bounds(n.get('b', ''))
        if not bounds:
            continue
        text = n.get('t', '').strip()
        if not text:
            continue
        x1, y1, x2, y2 = bounds
        text_items.append({
            'text': text,
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'cy': (y1 + y2) / 2.0,
        })
    
    if not text_items:
        return {'merchants': []}
    
    # Sort by y1 (top to bottom)
    text_items.sort(key=lambda t: (t['y1'], t['x1']))
    
    # Group text items by vertical proximity
    # Cards are separated by gaps > 30px between bottom of one item and top of next
    groups = []
    current_group = [text_items[0]]
    for item in text_items[1:]:
        gap = item['y1'] - current_group[-1]['y2']
        if gap > 30:
            groups.append(current_group)
            current_group = [item]
        else:
            current_group.append(item)
    groups.append(current_group)
    
    # Identify merchant card groups
    # A merchant card typically has: a name (long text with Chinese), 
    # a rating (like 4.7), and a distance (like 1.2km)
    merchants = []
    
    for group in groups:
        name = None
        rating = None
        distance = None
        
        # Sort group by y position
        group.sort(key=lambda t: t['y1'])
        
        for item in group:
            text = item['text']
            # Distance check first (most specific)
            if is_distance_text(text):
                distance = text
            # Rating check
            elif is_rating_text(text):
                rating = text
            # Name check - text with Chinese characters, longer than 2 chars
            # Must contain Chinese characters
            elif len(text) >= 2 and bool(re.search(r'[\u4e00-\u9fff]', text)):
                # Skip known non-name texts
                skip_words = {'分', '¥', '进店', '附近热卖', '月售', '人均', '起送', '配送',
                              '堂食店', '品质必点', '神券商家', '综合排序', '速度', '全部筛选',
                              '全部', '15分钟', '饺子馄饨', '汉堡薯条', '意面披萨', '包子粥店', '快餐便当',
                              '更多选择按钮', '购物车', '美团专送', '美团快送', '约15分钟',
                              '免配送费', '热销', '刚刚有用户看过', '拌饭必点人气款', '面也是一绝',
                              '鲜香嫩滑真美味', '份量充足', '所见即所得'}
                if text not in skip_words and not text.startswith('¥') and not text.startswith('['):
                    if name is None:
                        name = text
    
        if name and rating and distance:
            merchants.append({
                'name': name,
                'rating': rating,
                'distance': distance,
            })
    
    return {'merchants': merchants}
