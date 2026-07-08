# Meituan Food List Task

- Confirm the list is the configured `meituan/美食` page.
- Confirm city, address, and range before scanning merchants.
- Inspect visible merchant cards only to choose safe tap targets and list progress.
- Enter eligible merchants from title/avatar/icon areas, not coupon or campaign areas.
- After entering a merchant, set current page to `meituan/美食/商家`.
- After returning from a merchant, inspect the list and continue with the next eligible visible merchant or swipe the list.
- Stop the list only after two consecutive list swipes reveal no new merchants or a clear terminal marker appears.
- Do not leave `美食` for `外卖` until this list reaches its stop condition.
- If a persistent location-service reminder appears but the merchant list remains readable, continue normal list operation.
