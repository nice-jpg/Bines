---
page_strategy: general
---

# Meituan `外卖` Page

## Overview
- Takeout food merchant list page.
- This page is used to browse merchants under the takeout food category and enter eligible merchant detail pages.
- Config the page correctly. No need to collect.

## Canonical Manual Path
- Current page: `meituan/外卖/美食`.

## Structure
- The page is usually divided from top to bottom into a search box, category list, filter controls, and a merchant list.
- Before the true merchant list is available, this flow may first show an address chooser, current-location permission state, or a search-address page.
- After address and distance filters are active, visible merchant cards usually include a merchant name area, rating text, distance text, delivery metadata, promotion text, and possible product previews.

<!-- operation-notes:begin -->
## Operation Observations
## Operation Observations (美食 merchant list)

- Layout top-to-bottom: red header with back arrow (top-left) + title `美食`; white search box (`请输入商家或商品名称`); category chip row (美食/全部, 饺子馄饨, 汉堡薯条, ...); control row `综合排序` / `速度` / `全部筛选`; quick-filter chips (15分钟, 堂食店, 神券商家, 新客立减); then scrollable merchant card list.
- Each merchant card: thumbnail left, full merchant name + branch at top-right area with a `进店` button at card's upper-right; metadata lines (月售, 堂食店, 起送/配送费, distance, minutes, rating); promo tags; optional product preview thumbnails.
- Enter a merchant by tapping the card name area or the `进店` button; use navigate_to with next_path `meituan/外卖/美食/商家`.
- Floating cart button sits at bottom-right and can overlap card content; scroll before judging card edges.
- Back arrow in header returns to `meituan/外卖`.
- Filter row taps (综合排序, 全部筛选) are same-page interactions; `全部筛选` opens a panel needing a `完成` confirmation.
- Abstract regions may not reflect the visible layout well here; a screenshot is the fastest way to locate merchant cards and filter controls.
<!-- operation-notes:end -->
