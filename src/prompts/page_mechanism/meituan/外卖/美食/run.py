import re
import html


def run(xml: str) -> dict:
    # Parse all nodes
    nodes = []
    for part in xml.split('<n ')[1:]:
        b = re.search(r'b="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', part)
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        t = re.search(r't="([^"]*)"', part)
        d = re.search(r'd="([^"]*)"', part)
        c = re.search(r'c="([^"]*)"', part)
        k = re.search(r'\bk="([^"]*)"', part)
        text = html.unescape(t.group(1)).strip() if t else ''
        desc = html.unescape(d.group(1)).strip() if d else ''
        cls = c.group(1) if c else ''
        has_click = (k is not None and k.group(1) == '1')
        nodes.append({
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'text': text, 'desc': desc, 'class': cls,
            'clickable': has_click,
        })

    text_nodes = [n for n in nodes if n['text']]

    def is_rating(txt):
        return bool(re.fullmatch(r'[0-5](?:\.\d)?', txt))

    def is_distance(txt):
        return bool(re.fullmatch(r'\d+(?:\.\d+)?\s*(?:m|km)', txt.strip()))

    # Known non-merchant texts to skip
    skip_exact = {
        '更多', '全部', '综合排序', '速度', '全部筛选',
        '品质必点', '堂食店', '美团专送', '商家自配', '美团快送',
        '接受预订', '推荐', '排序', '筛选', '直播中',
        '请输入商家或商品名称',
        '饺子馄饨', '汉堡薯条', '意面披萨', '包子粥店', '快餐便当',
        '甜点饮品', '超市便利', '蔬菜水果', '看病买药', '拼好饭',
        '下午茶', '早餐', '天天津贴', '跑腿', '每日神价', '附近商家',
        '神券商家', '15分钟',
        '分',
    }

    def is_merchant_name(txt):
        if not txt:
            return False
        if txt in skip_exact:
            return False
        skip_prefixes = (
            '月售', '人均', '起送', '配送', '免配送费', '最高可用',
            '收藏领', '满', '新客', '已减', '到手价',
            '¥', '会员价', '约¥',
        )
        if any(txt.startswith(p) for p in skip_prefixes):
            return False
        if txt.startswith('[') and txt.endswith(']'):
            return False
        if is_rating(txt) or is_distance(txt):
            return False
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', txt))
        if chinese_count < 2:
            return False
        return True

    # 1. Find all rating nodes (easy to identify)
    ratings = []
    for n in text_nodes:
        if is_rating(n['text']) and n['x1'] >= 300:
            ratings.append(n)
    
    # Sort ratings by y position
    ratings.sort(key=lambda n: n['y1'])
    
    # 2. Group ratings into cards and find associated name and distance
    # Each card has one rating. Find the name above each rating, and distance within the same card.
    
    merchants = []
    
    for r_node in ratings:
        rating_y = r_node['y1']
        # Card top: search upward from rating for the name (within ~100px)
        # Card bottom: search downward from rating for distance (within ~200px)
        
        card_top = rating_y - 100
        card_bottom = rating_y + 250
        
        # Find name: look for text above the rating within the same card
        name = ''
        name_y = -1
        for n in text_nodes:
            if n['y1'] < rating_y and n['y1'] >= card_top and n['x1'] >= 300:
                if is_merchant_name(n['text']):
                    # Pick the closest to rating (the one just above it)
                    if n['y1'] > name_y:
                        name = n['text']
                        name_y = n['y1']
        
        # Find distance: look for distance text within the card area
        distance = ''
        for n in text_nodes:
            if n['y1'] >= rating_y and n['y2'] <= card_bottom and is_distance(n['text']):
                distance = n['text']
                break
        
        if name:
            merchants.append({
                'name': name,
                'rating': r_node['text'],
                'distance': distance,
            })
    
    return {'merchants': merchants}
