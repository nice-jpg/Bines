# Meituan Food Merchant Task

- Confirm this is a merchant detail page, not an ad, activity, coupon, or unrelated destination.
- If products are already visible, do not tap any tab or category.
- Only when the product area is not active, use at most one `tap_in_page` before scrolling to open a product-level tab such as `菜品` or `团购`.
- Never tap category anchors after entering the product area; they skip intermediate products.
- If no product area is visible, inspect visible tabs and open the single most direct product tab before treating the page as exhausted.
- Continue scrolling the same active product area until two consecutive product-area swipes reveal no new content or a clear terminal marker appears.
- Avoid tapping promotion, coupon, ad, review, or recommendation targets that leave the merchant detail workflow unless required to expose the product area.
- After the reachable product area reaches its stop condition, call `navigate_back(next_path="meituan/美食")`.
