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

## Operation Logic
- Verify this is the expected merchant detail page. If it is an activity, coupon, product, ad, or unrelated page, return to the list.
- Before the first product-list swipe, stabilize the page and identify a safe swipe lane inside the right product list. Prefer starting from the center-right product area, away from the bottom system gesture zone and away from screen edges.
- Never perform a long swipe that starts from the bottom navigation/gesture area, the extreme screen edge, or any area that can trigger Home/Desktop. If a swipe location is uncertain, use a shorter upward swipe from the center-right product area first.
- Collect merchant fields: name, rating, sales, distance, total review count, and positive review count. Scroll or open the review area when counts are not visible.
- Collect every reachable product: product name, price, and sales. Use 0 for missing product sales.
- Scan the right product list, tracking seen products. Swipe inside the right product list only; do not swipe the left category list.
- After each swipe, confirm you are still inside the same merchant page before continuing collection. If the page changed unexpectedly, recover immediately and resume the same merchant instead of concluding the task.
- Finish product collection only after 2 consecutive product-list swipes add no new products, or a clear product-list terminal marker appears.
- Do not leave this merchant until all merchant fields are resolved and all reachable products are recorded.
- Write all collected rows to the `result.xlsx` worksheet named with this merchant's complete name before using `swipe_back` to return to the list.
