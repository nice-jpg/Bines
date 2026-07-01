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
- Treat the configured secondary pages as an ordered run plan that must be executed before the run can be summarized as complete or handed back.
- Open each configured secondary page directly.
- Tap the entry only when it is visible and unobstructed; otherwise scroll it into view.
- After navigation, query the destination manual before continuing. If the page is wrong, return and retry.
- After one configured secondary page is finished, return to a page where the next configured secondary page can be opened, then continue immediately to that next page.
- Do not stop the overall run after partial progress on one configured secondary page. `美食` and `外卖` must both be attempted in the same run unless the interface truly blocks further navigation after inspection.
- Do not output a final or progress summary merely because one secondary page has produced some merchants or Excel writes. Summaries are for the end of the configured page sequence, not between its steps.
- If the current app state opens inside one configured secondary page instead of the home page, continue that page's workflow first, then deliberately navigate to the remaining configured secondary page rather than ending the run.
