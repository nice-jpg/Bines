# Meituan `美食` Merchant Page

## Canonical Manual Path
- Current page: `meituan/美食/商家`.
- This manual applies to any concrete merchant entered from `meituan/美食`.
- Do not replace `商家` with a concrete merchant name in query_manual.

## Structure
- The upper area usually contains basic merchant information and may hide after scrolling.
- The middle area usually contains tab titles.
- The lower area is usually split into a left category list and a right product list for the selected category.
- The left category list may scroll independently. The right product list is the product collection area.

## Operation Logic
- Verify this is the expected merchant detail page. If it is an activity, coupon, product, ad, or unrelated page, return to the list.
- Collect merchant fields: name, rating, sales, distance, total review count, and positive review count. Scroll or open the review area when counts are not visible.
- Collect every reachable product: product name, price, and sales. Use 0 for missing product sales.
- Scan the right product list, tracking seen products. Swipe inside the product list, not the left category list.
- Finish product collection only after 2 consecutive product-list swipes add no new products, or a clear product-list terminal marker appears.
- Do not leave this merchant until all merchant fields are resolved and all reachable products are recorded.
- Write all collected rows to the `result.xlsx` worksheet named with this merchant's complete name before using `swipe_back` to return to the list.
