from __future__ import annotations

import re


def run(xml: str, previous: dict | None = None) -> dict:
    if previous is None:
        previous = {"merchant": {"name": "", "rating": 0.0, "distance": "", "review_count": 0}, "products": []}

    # Parse all elements
    elements = []
    for match in re.finditer(r'<n\s+([^>]*?)\s*/>', xml):
        attrs_str = match.group(1)
        attrs = {}
        for am in re.finditer(r'(\w+)\s*=\s*"([^"]*?)"', attrs_str):
            attrs[am.group(1)] = am.group(2)
        if attrs:
            elements.append(attrs)

    def get_bounds(el):
        b = el.get("b", "")
        bm = re.findall(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', b)
        if bm:
            return tuple(map(int, bm[0]))
        return None

    # --- Merchant Info ---
    merchant = previous["merchant"]

    for el in elements:
        t = el.get("t", "")
        b = get_bounds(el)
        if t and b and b[1] < 400 and "齐家" in t:
            merchant["name"] = t
            break

    for el in elements:
        t = el.get("t", "")
        if "距您" in t:
            match = re.search(r'距您\s*([\d.]+[kK]?[mM]?)', t)
            if match:
                merchant["distance"] = match.group(1)
            break

    for el in elements:
        d = el.get("d", "")
        if "评价" in d and "条" in d:
            match = re.search(r'(\d+)条', d)
            if match:
                merchant["review_count"] = int(match.group(1))
            break

    merchant["rating"] = 0.0

    # --- Products ---
    # Collect text with bounds
    text_list = []
    for el in elements:
        t = el.get("t", "")
        b = get_bounds(el)
        if t and b:
            text_list.append({"text": t, "x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3]})

    # Find all elements that look like product names (right panel, x >= 500)
    # A product name is followed by "月售N" below it
    # Then by ¥ (main price) and ￥ (discount)
    
    # Find all "月售" occurrences to locate products
    monthly_sales_items = []
    for te in text_list:
        if te["x1"] >= 500 and te["text"].startswith("月售"):
            ms_match = re.search(r'月售(\d+)', te["text"])
            if ms_match:
                monthly_sales_items.append({
                    "y1": te["y1"],
                    "y2": te["y2"],
                    "x1": te["x1"],
                    "sales": int(ms_match.group(1))
                })

    # For each monthly sales, find the product name above it
    # The product name is the closest text element above the "月售" text
    # within the same card (within ~100px above)
    
    product_groups = []
    for ms_item in monthly_sales_items:
        ms_y = ms_item["y1"]
        ms_sales = ms_item["sales"]

        # Find product name: closest text above this monthly sales text
        # that's NOT a system element
        name = ""
        min_dist = 999
        for te in text_list:
            if te["x1"] < 500:
                continue
            txt = te["text"]
            # Must be above the monthly sales
            if te["y2"] >= ms_y:
                continue
            # Must be close (within 120px)
            dist = ms_y - te["y2"]
            if dist > 120 or dist < 0:
                continue

            # Filter non-product texts
            if txt in ("选规格", "到手价", "¥", "￥", "单点不送", "温馨提示：请适量点餐"):
                continue
            if txt.startswith("月售") or txt.startswith("约") or txt.startswith("、"):
                continue
            if txt.startswith("咸鲜") or txt.startswith("原味") or txt.startswith("美味"):
                continue
            if "人份" in txt:
                continue
            if re.match(r'^[\d.]+$', txt):
                continue

            if dist < min_dist:
                min_dist = dist
                name = txt

        if name:
            product_groups.append({
                "name": name,
                "ms_y": ms_y,
                "sales": ms_sales
            })

    # Remove duplicates (same name)
    seen = set()
    unique_products = []
    for pg in product_groups:
        if pg["name"] not in seen:
            seen.add(pg["name"])
            unique_products.append(pg)

    # Now extract prices for each product
    # Find ¥ and ￥ elements and match them to products by vertical proximity
    existing_names = {p["name"] for p in previous["products"]}
    new_products = []

    for pg in unique_products:
        name = pg["name"]
        ms_y = pg["ms_y"]
        monthly_sales = pg["sales"]

        # Find the product card area: from product name to next product name
        # Look for ¥ in the area ~50-200px below the monthly sales
        price_val = 0.0
        discount_val = 0.0

        # Find the closest ¥ below this product
        for te in text_list:
            if te["text"] == "¥" and te["x1"] >= 500:
                if te["y1"] > ms_y - 50 and te["y1"] < ms_y + 200:
                    # Get the number to the right
                    num_parts = []
                    for te2 in text_list:
                        if (abs(te2["y1"] - te["y1"]) <= 20 and
                            te2["x1"] >= te["x2"] and
                            re.match(r'^[\d.]+$', te2["text"])):
                            num_parts.append((te2["x1"], te2["text"]))
                    if num_parts:
                        num_parts.sort(key=lambda x: x[0])
                        try:
                            price_val = float("".join(p[1] for p in num_parts))
                        except:
                            pass
                    break

        # Find the closest ￥ below this product
        for te in text_list:
            if te["text"] == "￥" and te["x1"] >= 500:
                if te["y1"] > ms_y - 50 and te["y1"] < ms_y + 200:
                    num_parts = []
                    for te2 in text_list:
                        if (abs(te2["y1"] - te["y1"]) <= 20 and
                            te2["x1"] >= te["x2"] and
                            re.match(r'^[\d.]+$', te2["text"])):
                            num_parts.append((te2["x1"], te2["text"]))
                    if num_parts:
                        num_parts.sort(key=lambda x: x[0])
                        try:
                            discount_val = float("".join(p[1] for p in num_parts))
                        except:
                            pass
                    break

        if name not in existing_names:
            new_products.append({
                "name": name,
                "price": price_val,
                "original_price": price_val,
                "discount_price": discount_val,
                "monthly_sales": monthly_sales
            })
            existing_names.add(name)

    result = {"merchant": merchant, "products": previous["products"] + new_products}
    return result
