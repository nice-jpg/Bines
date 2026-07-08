import re
from typing import List, Dict, Any, Optional, Tuple


def parse_all_nodes(xml: str) -> List[Dict]:
    """Parse all <n .../> nodes preserving document order."""
    nodes = []
    for m in re.finditer(r'<n\s+([^>]*?)\s*(?:/>|</n>)', xml):
        attrs = m.group(1)
        node = {}
        bm = re.search(r'b="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', attrs)
        if bm:
            node["x1"] = int(bm.group(1))
            node["x2"] = int(bm.group(3))
            node["y1"] = int(bm.group(2))
            node["y2"] = int(bm.group(4))
        tm = re.search(r't="([^"]*)"', attrs)
        node["text"] = tm.group(1) if tm else ""
        dm = re.search(r'd="([^"]*)"', attrs)
        node["desc"] = dm.group(1) if dm else ""
        cm = re.search(r'c="([^"]*)"', attrs)
        node["cls"] = cm.group(1) if cm else ""
        km = re.search(r'k="([^"]*)"', attrs)
        node["key"] = km.group(1) if km else ""
        if "x1" in node:
            nodes.append(node)
    return nodes


def center(n: Dict) -> Tuple[float, float]:
    return ((n["x1"] + n["x2"]) / 2, (n["y1"] + n["y2"]) / 2)


def is_inside(n: Dict, x1: float, y1: float, x2: float, y2: float) -> bool:
    cx, cy = center(n)
    return x1 <= cx <= x2 and y1 <= cy <= y2


def x_overlap(n1: Dict, n2: Dict) -> bool:
    """Check if two nodes have overlapping x-ranges."""
    return n1["x1"] < n2["x2"] and n2["x1"] < n1["x2"]


UI_LABELS = {
    "评分", "月售", "外送", "自取", "点菜", "评价", "商家",
    "返回", "搜索", "收藏", "联系商家", "拼单", "更多",
    "美团专送", "已领", "推荐", "相册", "直播", "图片",
    "薅羊毛", "招牌必点", "单人餐", "券类核销",
    "省配送费", "正在讲解", "马上抢",
    "增加", "增加，已添加0份", "选套餐", "选规格",
    "单点不送", "1人份", "新客价", "到店价",
    "温馨提示：请适量点餐",
    "undefined 未选中", "小新福利",
    "堂食店", "为你推荐",
    "汉堡主食", "炸鸡小吃", "满足大串", "精品小串",
    "正新饮品", "正新鸡排", "炸鸡桶",
    "非常哇塞", "免起送价",
}


def is_ui_label(text: str) -> bool:
    if text in UI_LABELS:
        return True
    if re.match(r'^\d+减\d+$', text):
        return True
    if text in ("¥", "￥"):
        return True
    if re.match(r'^[\d.]+$', text):
        return True
    if text.startswith("比单点"):
        return True
    if text.startswith("距您"):
        return True
    if text == "到手价":
        return True
    if re.match(r'^约\d+分钟$', text):
        return True
    if text.startswith("满") and "可用" in text:
        return True
    if text == "已领":
        return True
    if text == "选套餐":
        return True
    if text.startswith("新客减"):
        return True
    if text == "省配送费":
        return True
    if text in ("外酥脆嫩", "原味、肉肠"):
        return True
    if text.startswith("￼"):
        return True
    return False


def collect_price_digits(nodes: List[Dict], start_idx: int, 
                          card_x1: float, card_y1: float, 
                          card_x2: float, card_y2: float) -> str:
    """Collect consecutive digit/decimal nodes after a position within card bounds.
    Skips non-text nodes. Detects row changes by checking if a new digit node
    starts significantly to the left of where the previous segment ended (indicating
    a new line/row for discount price)."""
    parts = []
    last_x2 = None
    for k in range(start_idx + 1, min(start_idx + 10, len(nodes))):
        nk = nodes[k]
        t = nk["text"]
        if not t:
            continue  # skip non-text nodes
        cxk, cyk = center(nk)
        if not (card_x1 <= cxk <= card_x2 and card_y1 <= cyk <= card_y2):
            break
        if t in ("¥", "￥", "起", "选套餐", "选规格", "增加", "增加，已添加0份",
                 "单点不送", "新客价", "到店价", "已领", "到手价"):
            break
        if t.startswith("比单点"):
            break
        if re.match(r'^[\d.]+$', t):
            # If the new digit starts significantly left of the previous end,
            # it's a new row (discount price below the regular price)
            if last_x2 is not None and nk["x1"] < last_x2 - 20:
                break
            parts.append(t)
            if last_x2 is None or nk["x2"] > last_x2:
                last_x2 = nk["x2"]
        else:
            break
    result = "".join(parts)
    while ".." in result:
        result = result.replace("..", ".")
    result = result.strip(".")
    return result


def collect_price_digits_backward(nodes: List[Dict], start_idx: int,
                                   card_x1: float, card_y1: float,
                                   card_x2: float, card_y2: float) -> str:
    """Collect digit nodes going backward from a node index within card bounds.
    Skips non-text nodes. Detects row changes by x-position."""
    parts = []
    first_x1 = None
    for k in range(start_idx - 1, max(start_idx - 10, -1), -1):
        nk = nodes[k]
        t = nk["text"]
        if not t:
            continue  # skip non-text nodes
        cxk, cyk = center(nk)
        if not (card_x1 <= cxk <= card_x2 and card_y1 <= cyk <= card_y2):
            break
        if t in ("¥", "￥"):
            break
        if re.match(r'^[\d.]+$', t):
            # Going backward: if this digit is significantly to the right of
            # the first collected digit, it's from a different row (the ¥ price row above)
            if first_x1 is not None and nk["x1"] > first_x1 + 20:
                break
            parts.insert(0, t)
            if first_x1 is None or nk["x1"] < first_x1:
                first_x1 = nk["x1"]
        else:
            break
    result = "".join(parts)
    while ".." in result:
        result = result.replace("..", ".")
    result = result.strip(".")
    return result


def run(xml: str) -> dict:
    """
    Extract merchant info and product items from a Meituan merchant detail page.

    Same-class page heuristic:
    - The page topology: top header (merchant info) → tab bar → main content
      (left sidebar + right product panel).
    - Tab bar contains "点菜" tab text; its y-position separates header from content.
    - Merchant name: long (>=5 chars) Chinese text in header area, often with （）.
    - Rating: text node near "评分" label.
    - Distance: text starting with "距您".
    - Product cards: ViewGroup (c="ViewGroup", k="1") in right panel area
      (center x > 400, width > 400, height > 40) AND below tab_y position.
    - Each card has: name (first non-UI Chinese text >= 3 chars),
      monthly_sales ("月售X"), ¥ price (original), and a discount price
      marked by ￥ or by a "新客价" label with nearby digits.
    - Price row detection: uses x-position changes to separate ¥ price row
      from discount price row below it.
    - Edge case: When ￥ symbol is cut off at bottom of viewport, find digits
      near "新客价" label going backward, using x-position to avoid crossing rows.
    """
    result = {
        "merchant": {"name": "", "rating": "", "distance": ""},
        "items": []
    }
    if not xml or not xml.strip():
        return result

    nodes = parse_all_nodes(xml)
    text_nodes = [n for n in nodes if n["text"]]

    # ========================
    # 1. FIND THE TAB BAR Y-POSITION
    # ========================
    tab_y = None
    for n in text_nodes:
        if n["text"] == "点菜":
            tab_y = n["y1"]
            break

    # ========================
    # 2. MERCHANT INFO (above tabs)
    # ========================
    merchant_header_nodes = []
    if tab_y is not None:
        merchant_header_nodes = [n for n in text_nodes if n["y2"] <= tab_y]
    else:
        header_bottom = 600
        merchant_header_nodes = [n for n in text_nodes if n["y1"] < header_bottom]

    # Merchant name: find long Chinese text with brackets, in header area
    merchant_name = ""
    for n in merchant_header_nodes:
        t = n["text"]
        if len(t) < 5:
            continue
        if is_ui_label(t):
            continue
        if not re.search(r'[\u4e00-\u9fff]', t):
            continue
        if t.startswith("【"):
            continue
        if "月售" in t:
            continue
        # Prefer names with brackets （）
        if re.search(r'[（(].*[）)]', t) and len(t) >= 5:
            merchant_name = t
            break

    if not merchant_name:
        for n in merchant_header_nodes:
            t = n["text"]
            if len(t) < 5:
                continue
            if is_ui_label(t):
                continue
            if t.startswith("【"):
                continue
            if "月售" in t:
                continue
            if re.search(r'[\u4e00-\u9fff]', t):
                merchant_name = t
                break

    result["merchant"]["name"] = merchant_name

    # Rating: find "评分" label, then find text node directly below it
    rating_label_node = None
    for n in merchant_header_nodes:
        if n["text"] == "评分":
            rating_label_node = n
            break

    if rating_label_node:
        for n in merchant_header_nodes:
            if x_overlap(rating_label_node, n):
                if rating_label_node["y1"] <= n["y1"] <= rating_label_node["y2"] + 60:
                    t = n["text"]
                    if t and t != "评分":
                        if re.match(r'^[\d.]+$', t):
                            result["merchant"]["rating"] = t
                        elif t == "暂无":
                            result["merchant"]["rating"] = ""
                        else:
                            result["merchant"]["rating"] = t
                        break

    # Distance: find "距您" text
    for n in merchant_header_nodes:
        t = n["text"]
        if "距您" in t:
            m = re.search(r'距您\s*([\d.]+[a-zA-Z]*)', t)
            result["merchant"]["distance"] = m.group(1) if m else t.replace("距您 ", "")
            break

    # ========================
    # 3. PRODUCT ITEMS
    # ========================

    # Find product card containers in the right panel, below the tab bar
    card_containers = []
    for n in nodes:
        if n["cls"] == "ViewGroup" and n.get("key") == "1":
            w = n["x2"] - n["x1"]
            h = n["y2"] - n["y1"]
            cx = (n["x1"] + n["x2"]) / 2
            
            # Must be in right panel (not left sidebar)
            if not (cx > 400 and w > 400 and h > 40):
                continue
            
            # Must be below the tab bar (product area)
            if tab_y is not None and n["y1"] < tab_y:
                continue
            
            card_containers.append(n)

    # Sort by y position
    card_containers.sort(key=lambda n: n["y1"])

    # Filter out cards that are contained within others
    filtered_cards = []
    for card in card_containers:
        is_contained = False
        for other in filtered_cards:
            if (card["x1"] >= other["x1"] and card["x2"] <= other["x2"] and
                card["y1"] >= other["y1"] and card["y2"] <= other["y2"]):
                is_contained = True
                break
        if not is_contained:
            filtered_cards.append(card)

    seen_names = set()

    for card in filtered_cards:
        cx1, cy1, cx2, cy2 = card["x1"], card["y1"], card["x2"], card["y2"]

        # Get text nodes inside this card
        card_texts = []
        for n in text_nodes:
            if is_inside(n, cx1, cy1, cx2, cy2):
                card_texts.append(n)

        if not card_texts:
            continue

        # Find product name: first non-UI-label Chinese text >= 3 chars
        product_name = ""
        name_node = None
        for n in card_texts:
            t = n["text"]
            if len(t) < 3:
                continue
            if is_ui_label(t):
                continue
            if not re.search(r'[\u4e00-\u9fff]', t):
                continue
            if t.startswith("月售"):
                continue
            if not product_name or n["y1"] < (name_node["y1"] if name_node else 9999):
                product_name = t
                name_node = n

        if not product_name or product_name in seen_names:
            continue
        seen_names.add(product_name)

        # Monthly sales: find "月售X" or "月售X+" text
        monthly_sales = ""
        for n in card_texts:
            t = n["text"]
            if t.startswith("月售"):
                m = re.search(r'月售(\d+)(\+?)', t)
                if m:
                    monthly_sales = m.group(1) + m.group(2)
                else:
                    monthly_sales = t.replace("月售", "")
                break

        # Find prices
        regular_price = ""
        discount_price = ""

        # Strategy 1: Find ¥ symbol and collect digits after it
        for i, n in enumerate(nodes):
            t = n["text"]
            if t == "¥" and is_inside(n, cx1, cy1, cx2, cy2):
                digits = collect_price_digits(nodes, i, cx1, cy1, cx2, cy2)
                if digits:
                    regular_price = digits

        # Strategy 2: Find ￥ symbol and collect digits after it
        for i, n in enumerate(nodes):
            t = n["text"]
            if t == "￥" and is_inside(n, cx1, cy1, cx2, cy2):
                digits = collect_price_digits(nodes, i, cx1, cy1, cx2, cy2)
                if digits:
                    discount_price = digits

        # Strategy 3: If no ￥ symbol found, look for "新客价" label
        # and collect digits backward
        if not discount_price:
            new_customer_node = None
            for n in card_texts:
                if n["text"] == "新客价":
                    new_customer_node = n
                    break
            if new_customer_node:
                found_idx = None
                for i, n in enumerate(nodes):
                    if (n["text"] == "新客价" and 
                        abs(n["x1"] - new_customer_node["x1"]) < 5 and
                        abs(n["y1"] - new_customer_node["y1"]) < 5):
                        found_idx = i
                        break
                
                if found_idx is not None:
                    digits = collect_price_digits_backward(nodes, found_idx, cx1, cy1, cx2, cy2)
                    if digits:
                        discount_price = digits

        price = regular_price if regular_price else discount_price
        original_price = regular_price if regular_price else (discount_price if discount_price else "")

        result["items"].append({
            "name": product_name,
            "price": price,
            "original_price": original_price,
            "discount_price": discount_price if discount_price != regular_price else "",
            "monthly_sales": monthly_sales,
        })

    return result
