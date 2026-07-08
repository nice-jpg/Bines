# Meituan Food Merchant Task

- Confirm this is a merchant detail page, not an ad, activity, coupon, or unrelated destination.
- Use tabs and scrolling to expose the product area described by PAGE.md.
- Prefer product-relevant tabs such as `菜品`, `团购`, `优惠`, `推荐`, or `套餐` when visible.
- If no product area is visible, inspect visible tabs and scroll each plausible product tab before treating the page as exhausted.
- Continue scrolling the active product area until two consecutive product-area swipes reveal no new content or a clear terminal marker appears.
- Avoid tapping promotion, coupon, ad, review, or recommendation targets that leave the merchant detail workflow unless required to expose the product area.
- After the reachable product area reaches its stop condition, return to the `meituan/美食` list.
