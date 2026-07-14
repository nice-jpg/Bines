import re


def run(xml: str) -> dict:
    nodes = _parse_xml(xml)
    merchant = _extract_merchant(nodes)
    items = _extract_items(nodes)
    return {"merchant": merchant, "items": items}


def _parse_xml(xml: str) -> list:
    nodes = []
    # XML format: < n b="..." ... /> (note space after <)
    for m in re.finditer(r'<\s*n\s+(.*?)\s*/>', xml):
        attrs = {}
        for am in re.finditer(r'(\w+)=("[^"]*"|\'[^\']*\')', m.group(1)):
            key = am.group(1)
            value = am.group(2)[1:-1]
            attrs[key] = value
        if 'b' in attrs:
            bm = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', attrs['b'])
            if bm:
                attrs['left'] = int(bm.group(1))
                attrs['top'] = int(bm.group(2))
                attrs['right'] = int(bm.group(3))
                attrs['bottom'] = int(bm.group(4))
        nodes.append(attrs)
    return nodes


def _get_text(n: dict) -> str:
    val = n.get('t')
    return val if isinstance(val, str) else ''


def _has_chinese(text: str) -> bool:
    return any('\u4e00' <= c <= '\u9fff' for c in text)


def _extract_merchant(nodes: list) -> dict:
    """Extract merchant name, rating, distance."""
    name = ""
    rating = ""
    distance = ""

    # Skip known labels/tabs/emojis
    skip_words = {
        '外送', '自取', '免起送价', '已领', '返回', '搜索', '收藏',
        '联系商家', '拼单', '更多', '点菜', '评价', '商家', '评分',
        '月售', '暂无', '美团专送', '堂食店', 'undefined',
        '温馨提示：请适量点餐', '推荐', '本店特色炒鸡', '炒菜，烧菜',
        '干锅系列', '凉菜', '地锅区', '主食', '饮料', '汤类',
    }

    name_candidates = []
    for n in nodes:
        text = _get_text(n)
        top = n.get('top', 0)
        left = n.get('left', 0)

        if text and len(text) >= 4 and top < 400 and left > 100 and left < 900:
            if text in skip_words:
                continue
            # Skip emoji-only or emoji-heavy text
            chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
            if chinese_count < 2:
                continue
            # Skip texts that start with common non-name prefixes
            if any(text.startswith(w) for w in ['满', '约', '距您', '¥', '￥']):
                continue
            name_candidates.append((top, left, text, chinese_count))

    if name_candidates:
        # Pick the one with most Chinese characters
        name_candidates.sort(key=lambda x: x[3], reverse=True)
        name = name_candidates[0][2]

    # Rating: find "暂无" text anywhere
    for n in nodes:
        text = _get_text(n)
        if text == '暂无':
            rating = '暂无'
            break

    # Distance: find text containing "距您"
    for n in nodes:
        text = _get_text(n)
        if '距您' in text:
            distance = text
            break

    return {"name": name, "rating": rating, "distance": distance}


def _extract_items(nodes: list) -> list:
    """Extract product items from the right-side product list."""
    items = []

    # Find all product names (Chinese text in the right panel, x ~ 500-600)
    product_entries = []
    for n in nodes:
        text = _get_text(n)
        left = n.get('left', 0)
        top = n.get('top', 0)

        if not (500 <= left <= 600):
            continue
        if not text or len(text) < 2:
            continue
        if not _has_chinese(text):
            continue

        skip_patterns = ['月售', '¥', '￥', '到手价', '约', '、',
                         '推荐', '温馨提示', 'undefined', '评分',
                         '暂无', '堂食店', '外送', '自取']
        should_skip = any(s in text for s in skip_patterns)
        if should_skip:
            continue

        product_entries.append({
            'name': text,
            'top': top,
        })

    product_entries.sort(key=lambda x: x['top'])

    for i, entry in enumerate(product_entries):
        item_top = entry['top']
        if i + 1 < len(product_entries):
            item_bottom = product_entries[i + 1]['top']
        else:
            item_bottom = 99999

        item_data = _extract_single_item(entry['name'], item_top, item_bottom, nodes)
        if item_data and (item_data.get('price') or item_data.get('monthly_sales')):
            items.append(item_data)

    return items


def _extract_single_item(name: str, item_top: int, item_bottom: int, nodes: list) -> dict:
    """Extract data for one item between vertical bounds."""
    result = {
        "name": name,
        "price": "",
        "original_price": "",
        "discount_price": "",
        "monthly_sales": ""
    }

    # Collect text nodes within item bounds, x >= 500
    text_nodes = []
    for n in nodes:
        t = n.get('top', 0)
        b = n.get('bottom', 0)
        l = n.get('left', 0)
        text = _get_text(n)
        if text and l >= 500 and t >= item_top - 10 and b <= item_bottom + 10:
            text_nodes.append({
                'text': text,
                'top': t,
                'bottom': b,
                'left': l,
                'right': n.get('right', 0)
            })

    # Monthly sales
    for tn in text_nodes:
        if tn['text'].startswith('月售'):
            result['monthly_sales'] = tn['text'][2:]
            break

    # Price: find ¥ symbol and collect adjacent number nodes
    for tn in text_nodes:
        if tn['text'] == '¥':
            sym_top = tn['top']
            sym_bottom = tn['bottom']
            sym_left = tn['left']

            numbers = []
            for tn2 in text_nodes:
                if tn2['text'] == '¥':
                    continue
                # Check vertical overlap
                if (tn2['top'] < sym_bottom + 5 and tn2['bottom'] > sym_top - 5
                        and tn2['left'] > sym_left):
                    if re.match(r'^[\d\.]+$', tn2['text']):
                        numbers.append(tn2)

            numbers.sort(key=lambda x: x['left'])
            price_str = ''.join(tn2['text'] for tn2 in numbers)
            if price_str:
                result['price'] = price_str
            break

    # Discount price (￥ symbol)
    for tn in text_nodes:
        if tn['text'] == '￥':
            sym_top = tn['top']
            sym_bottom = tn['bottom']
            sym_left = tn['left']

            numbers = []
            for tn2 in text_nodes:
                if tn2['text'] == '￥':
                    continue
                if (tn2['top'] < sym_bottom + 5 and tn2['bottom'] > sym_top - 5
                        and tn2['left'] > sym_left):
                    if re.match(r'^[\d\.]+$', tn2['text']):
                        numbers.append(tn2)

            numbers.sort(key=lambda x: x['left'])
            price_str = ''.join(tn2['text'] for tn2 in numbers)
            if price_str:
                result['discount_price'] = price_str
            break

    return result
