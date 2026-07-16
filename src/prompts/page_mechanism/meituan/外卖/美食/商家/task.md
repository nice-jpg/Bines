# Meituan `外卖/美食` Merchant Task

## Entry Check
- Confirm this is a merchant detail page entered from `meituan/外卖/美食`.
- If the destination is an ad, activity page, coupon page, standalone product page, blank page, or unrelated page, return to the list.
- If a popup blocks the page, close it with `tap_in_page` before continuing.
- If captcha or human verification appears, follow the global captcha flow.

## Page Operation
- Use XML first to identify safe controls and scroll areas; use screenshot only when XML is insufficient.
- If products are already visible, do not tap any tab or category.
- Only when the product area is not active, use at most one `tap_in_page` before scrolling to open a product-level entrance such as `菜品` or `团购`.
- Never tap left-side categories or category anchors. They jump over uncollected products.
- Once product scrolling starts, continuously use `swipe_up` in the product pane until a clear terminal marker appears or two consecutive swipes add no new products.
- Do not tap promotion/coupon/recommendation targets that leave the merchant detail workflow unless they are required to expose the merchant's product list.
- When the reachable product/menu area reaches the stop condition, call `navigate_back(next_path="meituan/外卖/美食")`.
