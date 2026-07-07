# Meituan Takeout List Task

- Confirm the list is the configured `meituan/外卖/美食` page.
- Inspect visible merchant cards only to choose safe tap targets and list progress.
- After entering a merchant, set current page to `meituan/外卖/美食/商家` and repeat PAGE/task/collector preparation.
- After returning from a merchant, inspect the list and continue with the next eligible visible merchant or swipe the list.
- Stop the list only after two consecutive list swipes reveal no new merchants or a clear terminal marker appears.
- Do not extract merchant fields directly; use page-local run.py JSON output.

## Collect task
- Focus on the merchants list. Collect merchant full name, rating, distance.
- Merchant may have a quite long full name. Be careful.

## Output format
Collections should be like:

``` json
{
  "merchants": [
    {
        "name": "炒面",
        "rating": 4.7,
        "distance": 1.0
    },
    {
        ...
    }
  ]
}
```

## Operations
1. Ignore the header.
2. Tap on filter `全部筛选`, select the best match in `速度` then tap `完成`.
3. Tap on `综合排序` and select `距离优先`.
4. Tap each merchant to enter a merchant.
5. Keep swipe up! Do not stop until all merchants are collected.
6. DO NOT stop and ask questions like: 'should I continue?'. The more data, the better result is.