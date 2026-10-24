# Meituan Food List Task

- Confirm the list is the configured `meituan/美食` page.
- Confirm city, address, and range before scanning merchants.
- Inspect visible merchant cards only to choose safe tap targets and list progress.
- Enter eligible merchants from title/avatar/icon areas, not coupon or campaign areas.
- Enter each merchant with `navigate_to(..., next_path="meituan/美食/商家", merchant_name=<full visible name including branch>)`.
- Lists can refresh or reorder after a return. Re-read the current cards; do not track completion by position or node ID. Use prior visit and collection facts to decide whether revisiting a merchant is useful.
- After returning from a merchant, inspect the list and continue with the next eligible visible merchant or swipe the list.
- Stop the list only after two consecutive list swipes reveal no new merchants or a clear terminal marker appears.
- Do not leave `美食` for `外卖` until this list reaches its stop condition.
- If a persistent location-service reminder appears but the merchant list remains readable, continue normal list operation.
