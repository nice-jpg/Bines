# Meituan Food Merchant Task

- Confirm this is a merchant detail page, not an ad, activity, coupon, or unrelated destination.
- If products are already visible, do not tap any tab or category.
- Only when the product area is not active, use at most one `tap_in_page` before scrolling to open a product-level tab such as `菜品` or `团购`.
- Never tap category anchors after entering the product area; they skip intermediate products.
- If no product area is visible, inspect visible tabs and open the single most direct product tab before treating the page as exhausted.
- Continuously use `swipe_up` in the same active product area and call `is_done` after every swipe.
- If `is_done.done` is false, continue swiping without changing tabs or categories.
- Treat `is_done.done=true` as the only collection completion signal; do not infer completion from terminal text or visible repetition.
- Avoid tapping promotion, coupon, ad, review, or recommendation targets that leave the merchant detail workflow unless required to expose the product area.
- Only after `is_done.done=true`, call `navigate_back(next_path="meituan/美食")`.
