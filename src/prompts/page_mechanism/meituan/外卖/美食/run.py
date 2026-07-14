import re


def run(xml: str) -> dict:
    """Extract merchant name, rating, distance from Meituan food listing page."""
    merchants = []

    # Find all <n ... /> nodes - simpler approach
    # Split by <n and process each
    parts = xml.split('<n ')
    
    nodes = []
    for part in parts[1:]:  # Skip everything before first <n
        # Extract bounds: b="[x1,y1][x2,y2]"
        b_match = re.search(r'b="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', part)
        if not b_match:
            continue
        x1 = int(b_match.group(1))
        y1 = int(b_match.group(2))
        x2 = int(b_match.group(3))
        y2 = int(b_match.group(4))
        
        # Extract text
        text = ''
        t_match = re.search(r't="([^"]*)"', part)
        if t_match:
            text = t_match.group(1).strip()
        
        nodes.append({
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'text': text
        })

    # Debug
    all_texts = [(n['text'], n['x1'], n['y1']) for n in nodes if n['text']]
    
    # Excluded texts (non-merchant texts)
    excluded = {
        '请输入商家或商品名称', '全部', '饺子馄饨', '汉堡薯条', '意面披萨', 
        '包子粥店', '快餐便当', '综合排序', '速度', '全部筛选',
        '品质必点', '堂食店', '神券商家', '15分钟',
        '美团专送', '美团快送', '商家自配', '分', '购物车',
    }

    # Find merchant names: Chinese text with x1 around 349, not in excluded list
    # and not starting with skip prefixes
    skip_prefixes = ('月售', '人均', '起送', '配送', '约', '满', '免',
                     '接受', '口味', '推荐', '份量', '折扣', '评论',
                     '最高可用', '收藏领')

    names_found = []
    for n in nodes:
        txt = n['text']
        if not txt:
            continue
        if txt in excluded:
            continue
        if not re.search(r'[\u4e00-\u9fff]', txt):
            continue
        if any(txt.startswith(p) for p in skip_prefixes):
            continue
        if len(txt) < 2:
            continue
        # Merchant names are positioned around x=349 (right side of card)
        if 300 <= n['x1'] <= 450:
            names_found.append(n)

    # Sort by y
    names_found.sort(key=lambda n: n['y1'])

    # Remove duplicates that are too close (same card region)
    unique_names = []
    for n in names_found:
        if not unique_names or n['y1'] - unique_names[-1]['y1'] >= 70:
            unique_names.append(n)

    # For each name, find rating and distance in the card region
    for i, name_node in enumerate(unique_names):
        name = name_node['text']
        card_top = name_node['y1']
        if i + 1 < len(unique_names):
            card_bottom = unique_names[i + 1]['y1'] - 5
        else:
            card_bottom = card_top + 400

        # Find rating (number like 4.8 followed by 分 nearby)
        rating = ''
        for n in nodes:
            if n['text'] and (n['y1'] >= card_top and n['y2'] <= card_bottom):
                if re.match(r'^[0-5](\.\d)?$', n['text']):
                    # Check for 分 nearby
                    for n2 in nodes:
                        if n2['text'] == '分' and abs(n2['y1'] - n['y1']) < 30:
                            rating = n['text']
                            break
                if rating:
                    break

        # Find distance
        distance = ''
        for n in nodes:
            if n['text'] and (n['y1'] >= card_top and n['y2'] <= card_bottom):
                if re.match(r'^[\d.]+(km|m)$', n['text']):
                    distance = n['text']
                    break

        merchants.append({
            'name': name,
            'rating': rating,
            'distance': distance,
        })

    return {'merchants': merchants}
