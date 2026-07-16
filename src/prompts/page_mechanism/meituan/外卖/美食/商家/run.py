import re


def run(xml: str) -> dict:
    nodes = _parse_xml(xml)
    return {"items": _extract_items(nodes)}


def _parse_xml(xml: str) -> list:
    nodes = []
    for m in re.finditer(r'<\s*n\s+(.*?)\s*/>', xml):
        attrs = {}
        for am in re.finditer(r'(\w+)=("[^"]*"|\'[^\']*\')', m.group(1)):
            attrs[am.group(1)] = am.group(2)[1:-1]
        left = top = right = bottom = 0
        bm = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', attrs.get('b', ''))
        if bm:
            left, top, right, bottom = map(int, bm.groups())
        attrs.update({"left": left, "top": top, "right": right, "bottom": bottom})
        nodes.append(attrs)
    return nodes


def _text(n: dict) -> str:
    return (n.get('t') or n.get('d') or '').strip()


def _find_right_panel(nodes: list) -> tuple:
    candidates = []
    for n in nodes:
        if n.get('c') == 'RecyclerView':
            l, t, r, b = n['left'], n['top'], n['right'], n['bottom']
            if l >= 200 and r >= 900 and b - t >= 350:
                candidates.append((l, t, r, b))
    if candidates:
        candidates.sort(key=lambda x: ((x[3] - x[1]), x[2] - x[0], -x[1]), reverse=True)
        return candidates[0]
    return (231, 1290, 1080, 2400)


def _texts_in_box(nodes: list, box: tuple) -> list:
    l0, t0, r0, b0 = box
    out = []
    for n in nodes:
        txt = _text(n)
        if not txt:
            continue
        l, t, r, b = n['left'], n['top'], n['right'], n['bottom']
        if l >= l0 and r <= r0 and t >= t0 and b <= b0:
            out.append({"text": txt, "left": l, "top": t, "right": r, "bottom": b})
    out.sort(key=lambda x: (x['top'], x['left']))
    return out


def _is_name_text(text: str) -> bool:
    if not text or len(text) < 2:
        return False
    bad_parts = [
        '月售', '半年售', '回头客推荐', '点评网友推荐', '网友推荐', '近期', '人已下单', '人觉得',
        '口感', '味道赞', '量非常', '温馨提示', '已选中', '未选中', '评分', '相册', '商家', '评价',
        '点菜', '选规格', '¥', '￥', '拼单'
    ]
    if any(x in text for x in bad_parts):
        return False
    if '“' in text or '”' in text or '"' in text:
        return False
    if re.fullmatch(r'[\d.+]+', text):
        return False
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return chinese >= 2


def _item_containers(nodes: list, panel: tuple) -> list:
    pl, pt, pr, pb = panel
    boxes = []
    for n in nodes:
        if n.get('c') != 'ViewGroup':
            continue
        l, t, r, b = n['left'], n['top'], n['right'], n['bottom']
        if l > pl + 10 or r < pr - 60:
            continue
        h = b - t
        if h < 120 and b < pb - 5:
            continue
        if t < pt:
            continue
        texts = _texts_in_box(nodes, (l, t, r, b))
        has_name = any(_is_name_text(x['text']) and x['left'] >= l + 250 for x in texts)
        has_price_or_sales = any(x['text'] in {'¥', '￥'} or re.search(r'[月半]售\s*\d', x['text']) for x in texts)
        if has_name and (has_price_or_sales or b >= pb - 5):
            boxes.append((l, t, r, b))
    boxes.sort(key=lambda x: (x[1], x[0]))
    dedup = []
    for box in boxes:
        if not dedup or box[1] >= dedup[-1][3] - 10:
            dedup.append(box)
        elif box[3] > dedup[-1][3]:
            dedup[-1] = box
    return dedup


def _pick_name(texts: list, left_floor: int) -> str:
    cands = [x for x in texts if x['left'] >= left_floor and _is_name_text(x['text'])]
    if not cands:
        return ''
    cands.sort(key=lambda x: (x['top'], x['left'], -(x['right'] - x['left'])))
    return cands[0]['text']


def _pick_monthly_sales(texts: list) -> str:
    for x in texts:
        m = re.search(r'([月半]售\s*[0-9]+\+?)', x['text'])
        if m:
            return m.group(1).replace(' ', '')
    return ''


def _pick_price(texts: list) -> str:
    marks = [x for x in texts if x['text'] in {'¥', '￥'}]
    if not marks:
        return ''
    mark = sorted(marks, key=lambda x: (x['top'], x['left']))[0]
    nums = []
    for x in texts:
        if x['left'] >= mark['right'] - 10 and abs(x['top'] - mark['top']) <= 30 and re.fullmatch(r'[\d.]+', x['text']):
            nums.append((x['left'], x['text']))
    nums.sort()
    return ''.join(v for _, v in nums)


def _extract_items(nodes: list) -> list:
    panel = _find_right_panel(nodes)
    items = []
    for box in _item_containers(nodes, panel):
        l, t, r, b = box
        texts = _texts_in_box(nodes, box)
        name = _pick_name(texts, l + 250)
        if not name:
            continue
        items.append({
            'name': name,
            'price': _pick_price(texts),
            'original_price': '',
            'discount_price': '',
            'monthly_sales': _pick_monthly_sales(texts),
        })
    return items
