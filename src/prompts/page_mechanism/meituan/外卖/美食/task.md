# Meituan Takeout List Task

- Confirm the list is the configured `meituan/外卖/美食` page.
- Inspect visible merchant cards only to choose safe tap targets and list progress.
- Enter each merchant with `navigate_to(..., next_path="meituan/外卖/美食/商家", merchant_name=<full visible name including branch>)`.
- Lists can refresh or reorder after a return. Re-read the current cards; do not track completion by position or node ID. Skip targets rejected as `merchant_already_collected`; unfinished merchants remain eligible.
- After returning from a merchant, inspect the list and continue with the next eligible visible merchant or swipe the list.
- Stop the list only after two consecutive list swipes reveal no new merchants or a clear terminal marker appears.

## Operations
1. Ignore the header.
2. Use `tap_in_page` on filter `全部筛选`, select the best match, then use `tap_in_page` on `完成`.
3. Use `tap_in_page` on `综合排序` and select `距离优先`.
4. Use `navigate_to` to enter each merchant.
5. Keep swiping the list until the page reaches the stop condition.
6. Do not stop and ask questions while normal list actions are still available.
