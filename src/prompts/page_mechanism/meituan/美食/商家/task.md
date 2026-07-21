# Meituan Food Merchant Task

- Confirm this is a merchant detail page, not an ad, activity, coupon, or unrelated destination.
- If products are already visible, do not tap any tab or category.
- Only when the product area is not active, use at most one `tap_in_page` before scrolling to open a product-level tab such as `菜品` or `团购`.
- Never tap category anchors after entering the product area; they skip intermediate products.
- If no product area is visible, inspect visible tabs and open the single most direct product tab before treating the page as exhausted.
- Continuously use `swipe_up` in the same active product area without calling `is_done` after ordinary swipes.
- Call `is_done` only when the page may be exhausted, such as after a bottom marker appears, a swipe causes no obvious movement, or visible product content remains repeated. Completion requires three consecutive valid collection swipes with no newly persisted items.
- If `is_done.done` is false, continue swiping the same product area; any newly persisted item resets the empty-swipe count. Wait for new possible-end evidence before checking again. Do not change tabs or categories.
- Treat `is_done.done=true` as the only collection completion authorization; visible ending clues only determine when to check.
- Avoid tapping promotion, coupon, ad, review, or recommendation targets that leave the merchant detail workflow unless required to expose the product area.
- Only after `is_done.done=true`, call `navigate_back(next_path="meituan/美食")`.
