# Meituan `美食` Merchant Page

## Canonical Manual Path
- Current page: `meituan/美食/商家`.
- This manual applies to any concrete merchant entered from `meituan/美食`.
- Do not replace `商家` with a concrete merchant name in query_manual.

## Structure
- The upper area usually contains basic merchant information and may hide after scrolling.
- The middle area usually contains tab titles such as `团购`, `菜品`, `评价`, `更多推荐`.
- The lower area may be either:
  - a true left-category + right-product layout, or
  - a single vertically scrolling recommendation / dish list where each visible dish card already counts as a product record.
- Product cards may show the price as a standalone `¥xx`, as an inline text fragment such as `19.82元 套餐名`, or may temporarily hide price until the card is fully visible.
- Sales may appear as a plain number near the right side of the card, not necessarily with a label.

## Required collection priority
1. First lock the merchant-level fields.
2. Then switch into the most product-complete area, preferring `菜品` over `团购` or `推荐` when available.
3. Only write Excel after product collection has reached a stop condition.

## Operation Logic
- Verify this is the expected merchant detail page. If it is an activity, coupon, product, ad, or unrelated page, return to the list.
- Collect merchant fields: name, rating, sales, distance, total review count, and positive review count. Scroll or open the review area when counts are not visible.
- Treat a visible dish card as one product. For each product, extract exactly one row with product name, price, and sales.
- Use product sales = `0` only when the product's sales value is truly missing after inspection.
- Do not use `0` as a fallback for missing price. If price is not yet visible, keep scrolling or re-center the card first; only leave price blank if the page still does not expose it.
- When a card contains multiple inline offer texts or combo descriptions, do not copy those offer texts as the product price unless they are the only visible price tied to that specific product. Prefer a standalone nearby `¥` price for the product card.
- If the current area mostly shows `团购`, `代金券`, `推荐菜`, `环境`, or editorial recommendation modules, do not treat that as full completion yet. First look for a more complete `菜品` or商品 list by checking visible tabs and continuing the in-page scroll.
- Do not conclude that a merchant has only a few products after one or two interactions. Before writing Excel, perform an explicit completeness pass: inspect the tab row, try the most product-like tab if present, then continue scrolling the merchant page/product area until 2 consecutive swipes add no new priced products.
- Scan the product list carefully, tracking seen product names to avoid duplicates.
- Continue swiping the product list until 2 consecutive product-list swipes add no new fully inspected products, or a clear terminal marker appears.
- Before deciding a merchant is complete, self-check: every visible product card on the current and previous swipe windows has been inspected for name, price, and sales, and no row uses an obviously wrong borrowed combo price.
- Do not leave this merchant until all required merchant fields are resolved and all reachable products are recorded.
- Write all collected rows to the `result.xlsx` worksheet named with this merchant's complete name before using `swipe_back` to return to the list.
