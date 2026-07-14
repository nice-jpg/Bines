import re

def run(xml: str) -> dict:
    """Extract merchant info and menu items from Meituan merchant page XML."""
    nodes = parse_xml(xml)
    merchant = extract_merchant(nodes)
    items = extract_items(nodes)
    return {"merchant": merchant, "items": items}


def parse_xml(xml):
    """Parse flat XML nodes into a list of dicts."""
    nodes = []
    for m in re.finditer(r'<n\s+(.*?)\s*/>', xml):
        attrs = {}
        attrs_str = m.group(1)
        for am in re.finditer(r'(\w+)=("[^"]*"|\'[^\']*\')', attrs_str):
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


def get_text(el):
    """Get text content from an element, returns empty string if absent."""
    val = el.get('t')
    return val if isinstance(val, str) else ''


def extract_merchant(nodes):
    """Extract merchant name, rating, distance."""
    name = ""
    rating = ""
    distance = ""
    
    name_candidates = []
    for n in nodes:
        text = get_text(n)
        top = n.get('top', 0)
        left = n.get('left', 0)
        
        if text and len(text) >= 2 and top < 500 and left > 200 and left < 1000:
            skip_words = {'外送', '自取', '免起送价', '已领', '返回', '搜索', '收藏', 
                         '联系商家', '拼单', '更多', '点菜', '评价', '商家', '评分',
                         '月售', '暂无', '美团专送', '堂食店', 'undefined',
                         '温馨提示：请适量点餐'}
            if text not in skip_words and not any(text.startswith(w) for w in ['满', '约', '距您', '¥', '￥']):
                if any('\u4e00' <= c <= '\u9fff' for c in text):
                    name_candidates.append((top, left, text))
    
    name_candidates.sort(key=lambda x: (x[0], x[1]))
    for top, left, text in name_candidates:
        if len(text) >= 4:
            name = text
            break
    
    for n in nodes:
        text = get_text(n)
        if text == '暂无':
            rating = '暂无'
            break
    
    for n in nodes:
        text = get_text(n)
        if '距您' in text:
            distance = text
            break
    
    return {"name": name, "rating": rating, "distance": distance}


def extract_items(nodes):
    """Extract product items using product names as anchors."""
    items = []
    
    # Find all product names
    product_entries = []
    for n in nodes:
        text = get_text(n)
        left = n.get('left', 0)
        top = n.get('top', 0)
        
        if 500 <= left <= 600 and text and len(text) >= 2:
            if any('\u4e00' <= c <= '\u9fff' for c in text):
                skip_patterns = ['月售', '¥', '￥', '到手价', '约', '、', '推荐', 
                                '温馨提示', 'undefined', '评分', '暂无', '堂食店', 
                                '外送', '自取']
                should_skip = any(s in text for s in skip_patterns)
                if not should_skip:
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
        
        item_data = extract_single_item(entry['name'], item_top, item_bottom, nodes)
        # Only include items that have at least a price or monthly sales (complete data)
        if item_data and (item_data.get('price') or item_data.get('monthly_sales')):
            items.append(item_data)
    
    return items


def extract_single_item(name, item_top, item_bottom, nodes):
    """Extract data for one item."""
    result = {
        "name": name,
        "price": "",
        "original_price": "",
        "discount_price": "",
        "monthly_sales": ""
    }
    
    # Collect all text nodes within this item's vertical bounds, x >= 500
    text_nodes = []
    for n in nodes:
        t = n.get('top', 0)
        b = n.get('bottom', 0)
        l = n.get('left', 0)
        text = get_text(n)
        
        if text and l >= 500 and t >= item_top - 10 and b <= item_bottom + 10:
            text_nodes.append({
                'text': text,
                'top': t,
                'bottom': b,
                'left': l,
                'right': n.get('right', 0)
            })
    
    # Find monthly sales
    for tn in text_nodes:
        if tn['text'].startswith('月售'):
            result['monthly_sales'] = tn['text'][2:]
            break
    
    # Find price symbols and numbers that vertically overlap
    for symbol_char in ['¥', '￥']:
        symbol_nodes = [tn for tn in text_nodes if tn['text'] == symbol_char]
        if not symbol_nodes:
            continue
        
        sym = symbol_nodes[0]
        sym_top = sym['top']
        sym_bottom = sym['bottom']
        sym_left = sym['left']
        
        numbers = []
        for tn in text_nodes:
            if tn['text'] == symbol_char:
                continue
            if (tn['top'] < sym_bottom and tn['bottom'] > sym_top):
                if re.match(r'^[\d\.]+$', tn['text']) and tn['left'] > sym_left:
                    numbers.append(tn)
        
        numbers.sort(key=lambda x: x['left'])
        price_str = ''.join(tn['text'] for tn in numbers)
        
        if symbol_char == '¥':
            if price_str:
                result['price'] = price_str
                result['original_price'] = price_str
        elif symbol_char == '￥':
            if price_str:
                result['discount_price'] = price_str
    
    return result
