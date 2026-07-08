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
    "非常哇塞",
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


def collect_price_digits(nodes: List[Dict], start_idx: int, card_x1: float, card_y1: float, card_x2: float, card_y2: float) -> str:
    """Collect consecutive digit/decimal nodes after a ¥ or ￥ symbol within card bounds."""
    parts = []
    for k in range(start_idx + 1, min(start_idx + 10, len(nodes))):
        nk = nodes[k]
        t = nk["text"]
        cxk, cyk = center(nk)
        if not (card_x1 <= cxk <= card_x2 and card_y1 <= cyk <= card_y2):
            break
        if t in ("¥", "￥", "起", "选套餐", "选规格", "增加", "增加，已添加0份",
                 "单点不送", "新客价", "到店价", "已领", "到手价"):
            break
        if t.startswith("比单点"):
            break
        if re.match(r'^[\d.]+$', t):
            parts.append(t)
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

    Page structure (same-class pages):
    - Top: merchant header area (merchant name, rating, distance, coupons)
    - Middle: tab bar ("点菜", "评价", "商家")
    - Below tabs: main content area with:
        - Left sidebar: category list (RecyclerView, x in [0, ~231])
        - Right panel: product items (RecyclerView/ScrollView, x in [~231, 1080])
    - Each product is inside a ViewGroup container (c="ViewGroup", k="1")
      positioned in the right panel area with significant height.
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
    # The "点菜" tab text marks the boundary between header and content.
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
        # Fallback: use first 30% of screen as header
        header_bottom = min(nodes, key=lambda n: n["y1"]).get("y2", 0) + 600 if nodes else 600
        merchant_header_nodes = [n for n in text_nodes if n["y1"] < header_bottom]

    # Merchant name: find long Chinese text, preferably with brackets
    merchant_name = ""
    for n in merchant_header_nodes:
        t = n["text"]
        if len(t) < 4:
            continue
        if is_ui_label(t):
            continue
        if not re.search(r'[\u4e00-\u9fff]', t):
            continue
        # Skip product-like names with 【】
        if t.startswith("【"):
            continue
        # Skip if it contains "月售"
        if "月售" in t:
            continue
        # Prefer names with brackets （）
        if re.search(r'[（(].*[）)]', t) and len(t) >= 5:
            merchant_name = t
            break

    if not merchant_name:
        # Fallback: first reasonable Chinese text
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

    # Rating: find "评分" label, then find nearest numeric value
    for n in merchant_header_nodes:
        if n["text"] == "评分":
            rx, ry = center(n)
            best_val = ""
            best_dist = 999
            for m in merchant_header_nodes:
                if re.match(r'^[\d.]+$', m["text"]):
                    mx, my = center(m)
                    if abs(mx - rx) < 200 and 0 <= my - ry <= 80:
                        dist = abs(mx - rx) + abs(my - ry)
                        if dist < best_dist:
                            best_dist = dist
                            best_val = m["text"]
            if best_val:
                result["merchant"]["rating"] = best_val
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

    # Find product card containers in the right panel
    # Cards are ViewGroup (c="ViewGroup") with k="1", in the right panel area
    # with significant height
    
    card_containers = []
    for n in nodes:
        if n["cls"] == "ViewGroup" and n.get("key") == "1":
            w = n["x2"] - n["x1"]
            h = n["y2"] - n["y1"]
            cx = (n["x1"] + n["x2"]) / 2
            
            # Right panel: x center > ~400 (not left sidebar)
            # Substantial size: width > 400, height > 150
            if cx > 400 and w > 400 and h > 150:
                card_containers.append(n)

    # Sort by y position
    card_containers.sort(key=lambda n: n["y1"])

    # Also skip cards that overlap too much - keep only distinct ones
    filtered_cards = []
    for card in card_containers:
        # Check if this card is mostly inside another card
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
        # that's not a category header (not "小新福利", "单人餐", etc.)
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
            if t in ("小新福利", "单人餐", "正新鸡排", "汉堡主食", "炸鸡小吃",
                     "满足大串", "精品小串", "正新饮品", "推荐", "券类核销",
                     "炸鸡桶"):
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

        # Find prices: look for ¥ and ￥ symbols within the card
        regular_price = ""
        discount_price = ""

        for i, n in enumerate(nodes):
            t = n["text"]
            if t == "¥" and is_inside(n, cx1, cy1, cx2, cy2):
                digits = collect_price_digits(nodes, i, cx1, cy1, cx2, cy2)
                if digits:
                    regular_price = digits

        for i, n in enumerate(nodes):
            t = n["text"]
            if t == "￥" and is_inside(n, cx1, cy1, cx2, cy2):
                digits = collect_price_digits(nodes, i, cx1, cy1, cx2, cy2)
                if digits:
                    discount_price = digits

        # Determine price mapping:
        # price = the ¥ price (current selling price)
        # original_price = the ¥ price (menu price, same if no discount)
        # discount_price = the ￥ price (discounted/new customer price)
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
