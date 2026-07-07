# Meituan Food Merchant Task

- Confirm this is a merchant detail page, not an ad, activity, coupon, or unrelated destination.
- Use tabs and scrolling to expose the product area described by PAGE.md.
- Continue operating until page-local run.py JSON indicates required merchant/product content is complete or unavailable.
- If run.py output is missing required fields or errors, call `prepare_collector` again before further collection-oriented operation.
- After the reachable product area reaches its stop condition, return to the `meituan/美食` list.
- Do not directly extract product names, prices, sales, ratings, or review counts.
