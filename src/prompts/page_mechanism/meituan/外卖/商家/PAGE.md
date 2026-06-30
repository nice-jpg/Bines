# Meituan `外卖` Merchant Page

## Canonical Manual Path
- Current page: `meituan/外卖/商家`.
- This manual applies to any concrete merchant entered from `meituan/外卖`.
- Do not replace `商家` with a concrete merchant name in query_manual.

## Structure
- The upper area usually contains basic merchant information and may hide after scrolling.
- The middle area usually contains tab titles.
- The lower area is usually split into a left category list and a right product list for the selected category.
- The left category list may scroll independently. The right product list is the product collection area.
- Some shops expose many categories. In those shops, the main failure mode is stopping after one category or a small sample from the first category.

## Operation Logic
- Verify this is the expected merchant detail page. If it is an activity, coupon, product, ad, or unrelated page, return to the list.
- First lock merchant fields: name, rating, sales, distance, total review count, and positive review count. If review counts are visible in tabs such as `评价(1331)`, record them immediately before scrolling.
- Collect every reachable product: product name, price, and sales. Use `0` for missing product sales only after the card itself was inspected and no sales text is shown.
- Scan the right product list, tracking seen product names. Swipe inside the right product list, not the left category list.
- For each visited category, keep scrolling the right product list until 2 consecutive right-list swipes add no new products, or a clear terminal marker appears for that category.
- After one category is exhausted, continue with the next unvisited left-side category instead of ending the merchant. Repeat until the visible left categories are exhausted; if the left category list itself can scroll, scroll that left list and continue with newly revealed categories.
- Do not stop because the first category already yielded some products. A merchant is complete only after category coverage is attempted across the product taxonomy, not just within one category.
- Before leaving the merchant, perform an explicit self-check: have I covered more than one category when multiple categories were visible, and have I written this merchant to Excel only after category traversal reached a stop condition?
- Do not leave this merchant until all merchant fields are resolved and all reachable products are recorded.
- Write all collected rows to the `result.xlsx` worksheet named with this merchant's complete name before using `swipe_back` to return to the list.
