# Meituan Food Merchant Task

- Confirm this is a merchant detail page, not an ad, activity, coupon, or unrelated destination.
- If products are already visible, do not tap any tab or category.
- Only when the product area is not active, use at most one `tap_in_page` before scrolling to open a product-level tab such as `菜品` or `团购`.
- Never tap category anchors after entering the product area; they skip intermediate products.
- If no product area is visible, inspect visible tabs and open the single most direct product tab before treating the page as exhausted.
- After successful collector training and top restoration, inspect the safe product swipe coordinates and call `collect_continuously(x, y, change_threshold)` with the exact `normal_swipe_profile.change_threshold` from training.
- The tool traverses with long swipes and short-swipes incomplete rows without per-step model calls. On `attention_required`, inspect the current page and resolve the issue before resuming; do not increase the threshold to hide anomalies. On `yielded`, follow incoming user instructions.
- If no verified normal-swipe profile is available, ask collector to sample normal short and long swipes; manual swipe tools remain available for recovery.
- During manual recovery, call `is_done` only when the page may be exhausted, such as after a bottom marker appears, a swipe causes no obvious movement, or visible product content remains repeated. Completion requires three consecutive valid collection swipes with no newly persisted items.
- If `is_done.done` is false, continue swiping the same product area; any newly persisted item resets the empty-swipe count. Wait for new possible-end evidence before checking again. Do not change tabs or categories.
- A `collect_continuously` result with `status="completed"` or manual `is_done.done=true` authorizes completion. Both use the same deterministic rule; visible ending clues alone do not authorize return.
- Avoid tapping promotion, coupon, ad, review, or recommendation targets that leave the merchant detail workflow unless required to expose the product area.
- Only after `collect_continuously.status="completed"` or `is_done.done=true`, call `navigate_back(next_path="meituan/美食")`.
