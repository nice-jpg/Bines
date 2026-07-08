from __future__ import annotations
import xml.etree.ElementTree as ET
import re


def _get_all_nodes(parent) -> list[dict]:
    """Flatten the XML tree into a list of node dicts in document order."""
    nodes = []
    for child in parent:
        attr = dict(child.attrib)
        nodes.append(attr)
        nodes.extend(_get_all_nodes(child))
    return nodes


def _find_merchant(all_nodes: list[dict]) -> dict:
    """Extract merchant name, distance, rating, review_count."""
    merchant = {"name": "", "distance": "", "rating": "", "review_count": ""}

    # ---- Find rating and review_count ----
    for i, node in enumerate(all_nodes):
        t = node.get("t", "")
        if re.match(r"^\d\.\d$", t):
            if i + 1 < len(all_nodes) and all_nodes[i + 1].get("t", "") == "分":
                merchant["rating"] = t
                for j in range(i + 2, min(i + 6, len(all_nodes))):
                    nt = all_nodes[j].get("t", "")
                    if nt.isdigit() and len(nt) >= 1:
                        merchant["review_count"] = nt
                        break
                break

    # ---- Find merchant name ----
    for node in all_nodes:
        t = node.get("t", "")
        d = node.get("d", "")
        if d:
            continue
        if len(t) >= 4 and re.search(r"[\u4e00-\u9fff]", t):
            known_labels = {"人气榜", "营业中", "优惠", "菜品", "服务", "评价",
                           "更多推荐", "相册", "商场", "打车", "电话",
                           "直播中", "随时退", "过期自动退", "抢购",
                           "回头客", "条评价", "分", "商家推荐",
                           "可停车", "可预订", "有宝宝椅"}
            if t.strip() in known_labels:
                continue
            if t.startswith("¥") or t.startswith("半年售") or t.startswith("平台券"):
                continue
            if t.startswith("已减"):
                continue
            clean = re.sub(r"[\d.]+$", "", t).strip()
            if clean and len(clean) >= 3:
                merchant["name"] = clean
                break

    return merchant


def _extract_items(all_nodes: list[dict]) -> list[dict]:
    """Extract product items from 团购菜品区域 containers."""
    items = []

    # Find container nodes with d="团购菜品区域"
    containers = []
    for node in all_nodes:
        if node.get("d") == "团购菜品区域" and node.get("k") == "1":
            containers.append(node.get("b", ""))

    for bounds_str in containers:
        if not bounds_str:
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
        if not m:
            continue
        x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

        # Collect all text nodes within this container's vertical bounds
        container_texts = []
        for node in all_nodes:
            t = node.get("t", "")
            if not t:
                continue
            nb = node.get("b", "")
            nm = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", nb)
            if not nm:
                continue
            ny, ny2 = int(nm.group(2)), int(nm.group(4))
            nx = int(nm.group(1))

            if y1 - 5 <= ny <= y2 + 5 and y1 - 5 <= ny2 <= y2 + 5:
                container_texts.append((ny, ny2, t, nx))

        # Sort by vertical position, then horizontal
        container_texts.sort(key=lambda x: (x[0], x[3]))

        # Extract structured data
        name_parts = []  # list of text pieces
        price = ""
        sales = ""

        for ny, ny2, t, nx in container_texts:
            # Skip known structural labels
            if t in ("随时退", "过期自动退", "抢购", "商家推荐", "¥"):
                continue

            # Sales text
            if "半年售" in t or "已售" in t or "月售" in t:
                sales = t
                continue

            # Discount coupons info
            if t.startswith("平台券") or t.startswith("已减"):
                continue

            # Discount rate like "7.1折"
            if re.match(r"^\d\.\d折$", t):
                continue

            # Original price like "¥99.8"
            if re.match(r"^¥\d+\.?\d*$", t):
                continue

            # Price per person like "¥72/人"
            if t.startswith("¥") and "人" in t:
                continue

            # Price value: standalone decimal number
            if re.match(r"^\d+\.\d+$", t):
                price = t
                continue

            # Any remaining Chinese text is part of product name
            if re.search(r"[\u4e00-\u9fff]", t) and len(t) >= 1:
                name_parts.append(t)
                continue

            # Other text might also be name parts (e.g. symbols, numbers in name)
            if t and not t.isdigit() and not re.match(r"^[\d.]+$", t):
                name_parts.append(t)

        # Build full name
        full_name = "".join(name_parts).strip()

        if full_name and price:
            items.append({
                "name": full_name,
                "price": price,
                "sales": sales,
            })

    return items


def run(xml: str) -> dict:
    root = ET.fromstring(xml)
    all_nodes = _get_all_nodes(root)

    merchant = _find_merchant(all_nodes)
    items = _extract_items(all_nodes)

    return {"merchant": merchant, "items": items}
