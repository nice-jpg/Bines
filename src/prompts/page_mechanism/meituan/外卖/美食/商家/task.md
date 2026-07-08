# Meituan `外卖/美食` Merchant Task

## Entry Check
- Confirm this is a merchant detail page entered from `meituan/外卖/美食`.
- If the destination is an ad, activity page, coupon page, standalone product page, blank page, or unrelated page, return to the list.
- If a popup blocks the page, close it before continuing.
- If captcha or human verification appears, follow the global captcha flow.

## Page Operation
- Use XML first to identify safe controls and scroll areas; use screenshot only when XML is insufficient.
- For single-list layouts, keep scrolling the product area until a clear terminal marker appears.
- Do not tap promotion/coupon/recommendation targets that leave the merchant detail workflow unless they are required to expose the merchant's product list.
- When the reachable product/menu area reaches the stop condition, return to `meituan/外卖/美食`.
