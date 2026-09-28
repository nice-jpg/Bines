# Meituan `外卖/美食` Merchant Page

## Overview
- Merchant detail page entered from `meituan/外卖/美食`.
- This page contains merchant identity and reachable product/menu information.

## Canonical Manual Path
- Current page: `meituan/外卖/美食/商家`.
- This manual applies to any concrete merchant entered from `meituan/外卖/美食`.
- Do not replace `商家` with a concrete merchant name in page paths.

## Structure
- The upper area usually contains merchant name, rating, distance, delivery/store metadata, and entrance controls.
- The middle area may contain tabs, category controls, filters, or promotion blocks.
- The lower area is the product/menu area. It may be:
  - a single vertical product list, often starting with '温馨提示：请适量点餐';
  - a left-category plus right-product layout;
  - a tabbed layout where product content changes by selected tab;
  - (maybe) an advertisement intruducing products in a inline form.
- Product cards may expose name, price, original price, discount/promotion price, sales text, and short descriptions.
- Some visible rows may be notices, policies, reminders, or promotion text rather than purchasable products.

## Collection Target
- Merchant fields: merchant name, rating, distance.
- Product fields: product name, price, original price, discount price, monthly sales.
- Product names, prices, and sales may be split across nearby view regions; the collector uses COLLECT.md annotations and screenshot-confirmed baselines to validate run(view).
- The same merchant page class may present a single product list, tabbed menu, or left-category/right-product layout.
- IMPORTANT: focus on infomation below '温馨提示：请适量点餐' when collecting products.

## Merchant Schema
```json
{
  "name": "",
  "rating": "",
  "distance": ""
}
```

The collector saves this profile from the initial top-of-page evidence. It is not returned by `run.agent.py` and is not part of item extraction scoring.

## Collection Schema
```json
{
  "items": [
    {
      "name": "",
      "current_price": "",
      "original_price": "",
      "monthly_sales": ""
    }
  ]
}
```

<!-- collection-contract:begin -->
## Collection Requirements
```json
{"primary_key": ["name"], "required_fields": ["name", "current_price"]}
```
<!-- collection-contract:end -->
