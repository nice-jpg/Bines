from __future__ import annotations

import re
from typing import Any


def parse_nodes(xml: str) -> list[dict[str, Any]]:
    """Parse the UIAutomator XML into a list of node dicts."""
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
    m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return None


def is_non_name(text: str) -> bool:
    """Check if text is definitely NOT a merchant name."""
    # Short strings
    if len(text) <= 1:
        return True
    # Price patterns
    if re.match(r'^¥', text):
        return True
    if re.match(r'^\d+\.\d+$', text) and len(text) <= 5:
        return True
    # Service/meta labels
    non_names = [
        r'^月售', r'^人均', r'^起送', r'^配送', r'^约',
        r'^\[', r'^分$', r'^更多选择', r'^堂食店',
        r'^免配送费', r'^10年老店', r'^商家自配', r'^美团专送',
        r'^美团快送', r'^\d+分钟', r'^\d+\.\d+km',
        r'^\d+m$', r'^去开启', r'^去使用', r'^搜索',
        r'^附近商家', r'^秒选外卖', r'^一键下单',
        r'^老朋友回归礼', r'^\d+月\d+日到期', r'^后失效',
        r'^满\d+可用', r'^已减', r'^到手价',
        r'^口味好评率', r'^近期', r'^都', r'^明厨亮灶',
        r'^热销', r'^今日推荐', r'^第三代', r'^脆皮', r'^徐州地锅',
        r'^最近24小时', r'^刚刚有用户看过', r'^全部',
        r'^饺子馄饨', r'^汉堡薯条', r'^意面披萨', r'^包子粥店',
        r'^快餐便当', r'^综合排序', r'^速度', r'^全部筛选',
        r'^15分钟', r'^品质必点', r'^神券商家', r'^进店',
        r'^四荤美味', r'^【',
    ]
    for p in non_names:
        if re.match(p, text):
            return True
    # Single numbers
    if re.match(r'^[45]\.\d$', text):
        return True
    return False


def is_rating(text: str) -> bool:
    return bool(re.match(r'^[45]\.\d$', text))


def is_distance(text: str) -> bool:
    return bool(re.match(r'^[\d.]+(?:km|m)$', text))


def run(xml: str) -> dict:
    nodes = parse_nodes(xml)
    
    # Collect all text nodes with positions
    text_items = []
    for n in nodes:
        bounds = parse_bounds(n.get('b', ''))
        if not bounds:
            continue
        text = n.get('t', '').strip()
        if not text:
            continue
        text_items.append({
            'text': text,
            'x1': bounds[0], 'y1': bounds[1], 'x2': bounds[2], 'y2': bounds[3],
            'cy': (bounds[1] + bounds[3]) // 2,
        })
    
    # Find the filter/header area bottom boundary.
    # After the filters (综合排序, 速度, 全部筛选) and sub-filters (15分钟, 品质必点, etc.),
    # the merchant cards start. We identify merchant-relevant text nodes that are
    # BELOW the filter section.
    # The filter bar and sub-filter chips are at y < ~800 in the scrolled state.
    # We can dynamically determine this by looking for the last filter-chip-like node.
    
    # Sort text items by y
    text_items.sort(key=lambda t: (t['y1'], t['x1']))
    
    # Identify all candidate name nodes (right-side, Chinese text)
    candidates = []
    for item in text_items:
        t = item['text']
        x1 = item['x1']
        
        # Must be on the right side (merchant details area starts at x≈349)
        if x1 < 300:
            continue
        
        # Must contain Chinese characters
        if not re.search(r'[\u4e00-\u9fff]', t):
            continue
        
        # Must not be a non-name
        if is_non_name(t):
            continue
        
        # Must be reasonably long (merchant names are typically 5+ chars)
        if len(t) < 4:
            continue
        
        # Heuristic: merchant name text node is usually wide (spans most of the card width)
        width = item['x2'] - item['x1']
        if width < 200:
            continue
        
        candidates.append(item)
    
    # Group candidates: if two candidates overlap vertically or are very close,
    # they're likely the same merchant (e.g., name split across lines).
    # Also, filter out candidates that are too close together (duplicates).
    
    # Sort candidates by y
    candidates.sort(key=lambda c: c['y1'])
    
    # For each candidate name, find rating and distance in the same merchant card area.
    # A merchant card typically spans from the name's top to about 200-300px below.
    
    merchants = []
    used_y_ranges = []  # Track y ranges already covered to avoid duplicates
    
    for cand in candidates:
        name_y1 = cand['y1']
        name_y2 = cand['y2']
        name_text = cand['text']
        
        # Check if this candidate is already covered by a previous merchant
        # (same merchant area)
        already_covered = False
        for y_start, y_end in used_y_ranges:
            if name_y1 >= y_start and name_y2 <= y_end:
                already_covered = True
                break
        if already_covered:
            continue
        
        # Estimate the card height (typically ~250-350px from name top to bottom of card)
        card_bottom = name_y1 + 320
        
        # Find rating in the same card area (between name bottom and card bottom,
        # on the right side x >= 300)
        rating = None
        rating_y = None
        for item in text_items:
            if item['y1'] < name_y2 - 5:  # should be below or at same level as name
                continue
            if item['y1'] > card_bottom:
                continue
            if item['x1'] < 300:
                continue
            if is_rating(item['text']):
                rating = item['text']
                rating_y = item['y1']
                break  # take the first rating below the name
        
        # Find distance in the same card area
        distance = None
        for item in text_items:
            if item['y1'] < name_y2 - 10:
                continue
            if item['y1'] > card_bottom:
                continue
            if is_distance(item['text']):
                # Distance should be roughly in the middle-right area (x between 700-1000)
                # but let's not be too strict
                distance = item['text']
                break  # take the first distance below the name
        
        if rating is not None and distance is not None:
            # Mark this y range as used
            used_y_ranges.append((name_y1, card_bottom))
            merchants.append({
                'name': name_text,
                'rating': rating,
                'distance': distance,
            })
    
    return {'merchants': merchants}
