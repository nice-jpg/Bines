# Meituan Food List Task

- Confirm the list is the configured `meituan/美食` page.
- Inspect visible merchant cards only to choose safe tap targets and list progress.
- Enter eligible merchants from title/avatar/icon areas, not coupon or campaign areas.
- After entering a merchant, set current page to `meituan/美食/商家` and repeat PAGE/task/collector preparation.
- After returning from a merchant, inspect the list and continue with the next eligible visible merchant or swipe the list.
- Stop the list only after two consecutive list swipes reveal no new merchants or a clear terminal marker appears.
- Do not extract merchant fields directly; use page-local run.py JSON output.
