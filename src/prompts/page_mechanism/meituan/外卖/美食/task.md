# Meituan Takeout List Task

- Confirm the list is the configured `meituan/外卖/美食` page.
- Inspect visible merchant cards only to choose safe tap targets and list progress.
- After entering a merchant, set current page to `meituan/外卖/美食/商家`.
- After returning from a merchant, inspect the list and continue with the next eligible visible merchant or swipe the list.
- Stop the list only after two consecutive list swipes reveal no new merchants or a clear terminal marker appears.

## Operations
1. Ignore the header.
2. Tap on filter `全部筛选`, select the best match in `速度` then tap `完成`.
3. Tap on `综合排序` and select `距离优先`.
4. Tap each merchant to enter a merchant.
5. Keep swiping the list until the page reaches the stop condition.
6. Do not stop and ask questions while normal list actions are still available.
