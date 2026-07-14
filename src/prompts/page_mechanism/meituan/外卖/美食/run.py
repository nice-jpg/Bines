import re


def run(xml: str) -> dict:
    """Extract merchant name, rating, distance from Meituan food listing page."""
    merchants = []

    # Parse all nodes with bounds and attributes
    node_re = re.compile(
        r'<n\s+b="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"([^>]*?)/?>'
    )

    nodes = []
    for m in node_re.finditer(xml):
        x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        attrs = m.group(5)
        text = ''
        t_match = re.search(r't="([^"]*)"', attrs)
        if t_match:
            text = t_match.group(1).strip()
        cls = ''
        c_match = re.search(r'c="([^"]*)"', attrs)
        if c_match:
            cls = c_match.group(1)
        has_k1 = 'k="1"' in attrs
        nodes.append({
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'text': text, 'cls': cls, 'k1': has_k1
        })

    # Debug
    frame_k1_nodes = [n for n in nodes if n['cls'] == 'FrameLayout' and n['k1']]
    right_frame_k1 = [n for n in frame_k1_nodes if 300 <= n['x1'] <= 400]
    
    debug = {
        'total_nodes': len(nodes),
        'frame_k1_count': len(frame_k1_nodes),
        'right_frame_k1_count': len(right_frame_k1),
        'right_frame_k1_positions': [(n['x1'], n['y1'], n['x2'], n['y2']) for n in right_frame_k1],
        'text_nodes_sample': [(n['text'], n['x1'], n['y1']) for n in nodes if n['text']][:20]
    }

    # Find card containers: FrameLayout with k="1" on the right side (x1 around 349)
    card_containers = right_frame_k1
    # Sort by y position
    card_containers.sort(key=lambda n: n['y1'])

    # Filter to find merchant cards (they have a name text node inside)
    for card in card_containers:
        cx1, cy1, cx2, cy2 = card['x1'], card['y1'], card['x2'], card['y2']

        # Find text nodes within this card
        card_texts = [
            n for n in nodes
            if n['text']
            and n['x1'] >= cx1 - 10 and n['x2'] <= cx2 + 10
            and n['y1'] >= cy1 - 5 and n['y2'] <= cy2 + 10
        ]

        if not card_texts:
            continue

        card_texts.sort(key=lambda n: (n['y1'], n['x1']))

        name = ''
        rating = ''
        distance = ''

        for t in card_texts:
            txt = t['text']

            # Name: first text node with Chinese characters at the top of card
            if not name and re.search(r'[\u4e00-\u9fff]', txt):
                # Check if it's near the top of the card (within first 80px)
                if t['y1'] - cy1 <= 80:
                    # Filter out known non-name texts
                    skip_prefixes = ('月售', '人均', '起送', '约', '满', '免',
                                     '接受', '10:', '15:', '20:', '堂食',
                                     '分', '综合', '速度', '全部', '品质',
                                     '神券', '15分钟', '配送')
                    if not any(txt.startswith(p) for p in skip_prefixes):
                        # Also filter out pure numbers/short texts
                        if len(txt) >= 2:
                            name = txt

            # Rating: matches pattern like "4.6", "4.8", "5.0"
            if not rating and re.match(r'^[0-5](\.\d)?$', txt):
                # Verify there's a "分" node nearby (within 20px vertically)
                for t2 in card_texts:
                    if t2['text'] == '分' and abs(t2['y1'] - t['y1']) < 20:
                        rating = txt
                        break

            # Distance: text ending with km, m
            if not distance:
                dist_match = re.match(r'^([\d.]+)(km|m)$', txt)
                if dist_match:
                    distance = txt

        if name:
            merchants.append({
                'name': name,
                'rating': rating,
                'distance': distance,
            })

    return {'merchants': merchants, 'debug': debug}
