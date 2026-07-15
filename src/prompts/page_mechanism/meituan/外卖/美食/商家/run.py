import re


def run(xml: str) -> dict:
    nodes = _parse_xml(xml)
    merchant = _extract_merchant(nodes)
    items = _extract_items(nodes)
    return {"merchant": merchant, "items": items}


def _parse_xml(xml: str) -> list:
    nodes = []
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
        else:
            attrs['left'] = 0
            attrs['top'] = 0
            attrs['right'] = 0
            attrs['bottom'] = 0
        nodes.append(attrs)
    return nodes


def _get_text(n: dict) -> str:
    val = n.get('t')
    return val if isinstance(val, str) else ''


def _has_chinese(text: str) -> bool:
    return any('\u4e00' <= c <= '\u9fff' for c in text)


def _get_node_bounds(n: dict) -> tuple:
    return (n.get('left', 0), n.get('top', 0),
            n.get('right', 0), n.get('bottom', 0))


def _find_right_panel_top(nodes: list) -> int:
    """Find the top of the right panel area by looking for the category area."""
    # Look for the first item-like container or RecyclerView in right panel
    # The right panel usually starts after the tab bar
    for n in nodes:
        c = n.get('c', '')
        if c == 'RecyclerView' or c == 'ScrollView':
            left = n.get('left', 0)
            top = n.get('top', 0)
            if left >= 200 and top > 0:
                return top
    # Fallback: look for "温馨提示" text which marks items start
    for n in nodes:
        text = _get_text(n)
        if '温馨提示' in text:
            return n.get('top', 0) + 50
    return 500


def _extract_merchant(nodes: list) -> dict:
    """Extract merchant name, rating, distance from the header area."""
    name = ""
    rating = ""
    distance = ""

    # Find merchant name: longest Chinese text at top of page
    candidates = []
    for n in nodes:
        text = _get_text(n)
        top = n.get('top', 0)
        left = n.get('left', 0)
        right = n.get('right', 0)
        if not text or top > 600 or top < 10:
            continue
        if not _has_chinese(text):
            continue
        chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        if chinese_count >= 4 and right - left >= 200:
            skip_words = [
                '下拉', '进入直播间', '直播中', '相册', '团购', '菜品',
                '评价', '会员', '推荐', '更多推荐', '超值券', '到店团购',
                '营业中', '收录', '条评价', '人', '地址', '美团', '到店',
                '评分', '月售', '外送', '自取', '免起送价', '搜索',
                '收藏', '联系商家', '拼单', '更多', '返回', '商家背景墙',
                '温馨提示'
            ]
            if any(s in text for s in skip_words):
                continue
            if text.startswith('¥') or text.startswith('￥'):
                continue
            if len(text) >= 4:
                candidates.append((chinese_count, text))

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        name = candidates[0][1]

    # Find rating: look for a number near "评分" or "分" text
    rating_found = False
    for n in nodes:
        text = _get_text(n)
        top = n.get('top', 0)
        if text and re.match(r'^\d+(\.\d+)?$', text) and top < 600:
            num_top = top
            num_left = n.get('left', 0)
            for n2 in nodes:
                t2 = _get_text(n2)
                if '评分' in t2 or t2 == '分':
                    n2_top = n2.get('top', 0)
                    n2_right = n2.get('right', 0)
                    if abs(n2_top - num_top) < 60 and abs(n2_right - num_left) < 80:
                        rating = text
                        rating_found = True
                        break
        if rating_found:
            break

    if not rating_found:
        for n in nodes:
            text = _get_text(n)
            if '评分' in text or text == '分':
                top = n.get('top', 0)
                right = n.get('right', 0)
                for n2 in nodes:
                    t2 = _get_text(n2)
                    if t2 and re.match(r'^\d+(\.\d+)?$', t2):
                        n2_top = n2.get('top', 0)
                        n2_left = n2.get('left', 0)
                        if abs(n2_top - top) < 60 and n2_left - right < 80 and n2_left - right >= 0:
                            rating = t2
                            rating_found = True
                            break
            if rating_found:
                break

    # Find distance
    for n in nodes:
        text = _get_text(n)
        if text and ('距' in text or 'km' in text.lower() or '米' in text):
            top = n.get('top', 0)
            if top < 1300:
                distance = text
                break

    return {"name": name, "rating": rating, "distance": distance}


def _extract_price_from_texts(texts_in: list) -> list:
    """
    Extract price from a list of text nodes.
    Handles split price nodes like "¥", "0.", "1" => "0.1"
    Also handles "¥0.1" in one node.
    """
    combined_prices = []

    # Find all ¥/￥ nodes
    yuen_nodes = [t for t in texts_in if t['text'] in ('¥', '￥')]

    for yn in yuen_nodes:
        yn_top = yn['top']
        yn_right = yn['right']

        # Collect all digit/decimal nodes that are near the ¥ sign
        digit_parts = []
        for t in texts_in:
            text = t['text']
            if text in ('¥', '￥'):
                continue
            if abs(t['top'] - yn_top) < 60 and t['left'] >= yn_right - 20:
                if re.match(r'^[\d.]+$', text):
                    digit_parts.append((t['left'], text))

        # Sort by left position to assemble digits in order
        digit_parts.sort(key=lambda x: x[0])
        if digit_parts:
            full_price = ''.join(p[1] for p in digit_parts)
            if full_price:
                combined_prices.append(full_price)

    # Also check for nodes with "¥" embedded in text like "¥0.1"
    for t in texts_in:
        text = t['text']
        m = re.search(r'[¥￥]([\d.]+)', text)
        if m:
            combined_prices.append(m.group(1))

    return combined_prices


def _find_item_containers_dynamic(nodes: list) -> list:
    """
    Find item containers in the right panel dynamically.
    Looks for ViewGroups that could be item cards.
    """
    # First, find the right panel area (RecyclerView or scroll area)
    panel_top = _find_right_panel_top(nodes)

    containers = []
    for n in nodes:
        c = n.get('c', '')
        left = n.get('left', 0)
        right = n.get('right', 0)
        top = n.get('top', 0)
        bottom = n.get('bottom', 0)

        # Item containers are ViewGroups in the right panel area
        # They have left ~200-300, right >= 1000, height >= 80
        if (c == 'ViewGroup'
                and 180 <= left <= 350
                and right - left >= 700
                and top >= panel_top - 50
                and bottom - top >= 80
                and bottom <= 2600):
            containers.append({
                'top': top,
                'bottom': bottom,
                'left': left,
                'right': right,
            })

    if not containers:
        # Fallback: any wide ViewGroup in right side
        for n in nodes:
            c = n.get('c', '')
            left = n.get('left', 0)
            right = n.get('right', 0)
            top = n.get('top', 0)
            bottom = n.get('bottom', 0)
            if (c == 'ViewGroup'
                    and left >= 180
                    and right >= 1000
                    and top >= panel_top - 50
                    and bottom - top >= 80
                    and bottom <= 2600):
                containers.append({
                    'top': top,
                    'bottom': bottom,
                    'left': left,
                    'right': right,
                })

    # Sort and deduplicate
    containers.sort(key=lambda x: x['top'])
    filtered = []
    for c in containers:
        if not filtered or c['top'] >= filtered[-1]['bottom'] - 30:
            filtered.append(c)
    return filtered


def _extract_items(nodes: list) -> list:
    """Extract product items from the product list area."""
    containers = _find_item_containers_dynamic(nodes)

    if containers:
        items = []
        for container in containers:
            item = _extract_item_from_container(container, nodes)
            if item and item.get('name'):
                items.append(item)
        return items

    return _extract_items_by_grouping(nodes)


def _extract_item_from_container(container: dict, nodes: list) -> dict:
    """Extract item data from a product container."""
    c_top = container['top']
    c_bottom = container['bottom']
    c_left = container['left']
    c_right = container['right']

    texts_in = []
    for n in nodes:
        text = _get_text(n)
        if not text:
            continue
        t = n.get('top', 0)
        b = n.get('bottom', 0)
        l = n.get('left', 0)
        r = n.get('right', 0)
        if (t >= c_top - 5 and b <= c_bottom + 5 and
                l >= c_left and r <= c_right):
            texts_in.append({
                'text': text,
                'left': l,
                'top': t,
                'bottom': b,
                'right': r,
            })

    if not texts_in:
        return {}

    texts_in.sort(key=lambda x: (x['top'], x['left']))

    # Find product name: long Chinese text with left > 400
    name = ''
    for t in texts_in:
        text = t['text']
        if _has_chinese(text) and len(text) >= 4 and t['left'] > 400:
            chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
            if chinese_count >= 3:
                skip_words = ['会员专属', '美团补贴', '共省', '月售', '半年售',
                             '限量', '本季', '单次', '限时', '本季新品',
                             '推荐', '超值券', '到店团购', '温馨提示',
                             '集满', '再下', '得', '无门槛', '代金券',
                             '近期', '人觉得', '单点不送']
                if not any(s in text for s in skip_words):
                    name = text
                    break

    if not name:
        return {}

    # Find monthly sales
    monthly_sales = ''
    for t in texts_in:
        text = t['text']
        if text.startswith('半年售') or text.startswith('月售'):
            monthly_sales = text
            break

    # Find prices
    price = ''
    original_price = ''
    discount_price = ''

    price_parts = _extract_price_from_texts(texts_in)
    if price_parts:
        price = price_parts[0]
        if len(price_parts) > 1:
            original_price = price_parts[1]

    return {
        "name": name,
        "price": price,
        "original_price": original_price,
        "discount_price": discount_price,
        "monthly_sales": monthly_sales,
    }


def _extract_items_by_grouping(nodes: list) -> list:
    """Fallback: group texts by proximity to find items."""
    panel_top = _find_right_panel_top(nodes)

    texts = []
    for n in nodes:
        text = _get_text(n)
        if not text:
            continue
        left = n.get('left', 0)
        top = n.get('top', 0)
        if left < 200 or top < panel_top - 50:
            continue
        texts.append({
            'text': text,
            'left': left,
            'top': top,
            'bottom': n.get('bottom', 0),
            'right': n.get('right', 0),
        })

    texts.sort(key=lambda x: (x['top'], x['left']))

    name_nodes = []
    for t in texts:
        text = t['text']
        if _has_chinese(text) and len(text) >= 4 and t['left'] > 400:
            chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
            if chinese_count >= 3:
                skip_words = ['会员专属', '美团补贴', '共省', '月售', '半年售',
                             '限量', '本季', '单次', '限时', '本季新品',
                             '推荐', '超值券', '到店团购', '温馨提示',
                             '集满', '再下', '得', '无门槛', '代金券',
                             '近期', '人觉得', '单点不送']
                if not any(s in text for s in skip_words):
                    name_nodes.append(t)

    items = []
    for i, nn in enumerate(name_nodes):
        name_top = nn['top']
        if i + 1 < len(name_nodes):
            next_top = name_nodes[i + 1]['top']
        else:
            next_top = 99999

        group = [t for t in texts
                 if t['top'] >= name_top - 5 and t['top'] < next_top]

        item = _extract_item_data(group, nn['text'])
        if item.get('name'):
            items.append(item)

    return items


def _extract_item_data(group: list, name: str) -> dict:
    monthly_sales = ''
    price = ''
    original_price = ''
    discount_price = ''

    price_parts = _extract_price_from_texts(group)
    if price_parts:
        price = price_parts[0]
        if len(price_parts) > 1:
            original_price = price_parts[1]

    for t in group:
        text = t['text']
        if text.startswith('半年售') or text.startswith('月售'):
            monthly_sales = text
            break

    return {
        "name": name,
        "price": price,
        "original_price": original_price,
        "discount_price": discount_price,
        "monthly_sales": monthly_sales,
    }
