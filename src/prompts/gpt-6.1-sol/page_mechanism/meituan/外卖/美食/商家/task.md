# Meituan `外卖/美食` Merchant Task

## Entry Check
- Confirm this is a merchant detail page entered from `meituan/外卖/美食`.
- If the destination is an ad, activity page, coupon page, standalone product page, blank page, or unrelated page, return to the list.
- If a popup blocks the page, close it with `tap_in_page` before continuing.
- If captcha or human verification appears, follow the global captcha flow.

## Page Operation
- Use page_view, page_read and page_node first to identify safe controls and scroll areas; use screenshot only when the abstract view is insufficient.
- If products are already visible, do not tap any tab or category.
- Only when the product area is not active, use at most one `tap_in_page` before scrolling to open a product-level entrance such as `菜品` or `团购`.
- Never tap left-side categories or category anchors. They jump over uncollected products.
- After successful collector training and top restoration, inspect the safe product swipe coordinates and call `collect_continuously(x, y, change_threshold)` with the exact `normal_swipe_profile.change_threshold` from training.
- The tool traverses with long swipes and short-swipes incomplete rows without per-step model calls. On `attention_required`, inspect the current page and resolve the issue before resuming; do not increase the threshold to hide anomalies. On `yielded`, follow incoming user instructions.
- If no verified normal-swipe profile is available, ask collector to sample normal short and long swipes; manual swipe tools remain available for recovery.
- During manual recovery, call `is_done` only when the page may be exhausted, such as after a bottom marker appears, a swipe causes no obvious movement, or visible product content remains repeated. Completion requires three consecutive valid collection swipes with no newly persisted items.
- If `is_done.done` is false, continue swiping the same product pane; any newly persisted item resets the empty-swipe count. Wait for new possible-end evidence before checking again. Do not switch categories or return.
- A `collect_continuously` result with `status="completed"` or manual `is_done.done=true` authorizes completion. Both use the same deterministic rule; visible ending clues alone do not authorize return.
- Do not tap promotion/coupon/recommendation targets that leave the merchant detail workflow unless they are required to expose the merchant's product list.
- Only after `collect_continuously.status="completed"` or `is_done.done=true`, call `navigate_back(next_path="meituan/外卖/美食")`.
