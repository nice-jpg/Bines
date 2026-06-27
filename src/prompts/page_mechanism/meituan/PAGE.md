# Meituan Home Page

## Canonical Manual Path
- Current page: `meituan`.
- Next secondary pages use their configured page type paths, for example `meituan/美食` and `meituan/外卖`.
- Do not query `meituan/首页` or `美团/首页`; the home manual path is the app path itself.

## Structure
- The page contains a hidden drawer, which appears latter to main frame. Do not pay attention to it.
- The page contains configured secondary entries such as `美食` and `外卖`.
- XML may not match the visible layout; use screenshot when target position is unclear.

## Operation Logic
- Open each configured secondary page directly.
- Tap the entry only when it is visible and unobstructed; otherwise scroll it into view.
- After navigation, query the destination manual before continuing. If the page is wrong, return and retry.
