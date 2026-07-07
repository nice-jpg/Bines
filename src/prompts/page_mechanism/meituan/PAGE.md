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
- If the app opens inside a leftover list, merchant detail, or tab state from a previous attempt, do not treat that stale page as the fresh starting point for a new run. First recover to a stable entry context where the configured page order, address, and range can be re-applied deliberately.
- In particular, if launch lands inside `美食/商家`, `外卖/商家`, or another already-open secondary subpage, prefer backing out to the corresponding list or home page before creating new collection state or deciding which configured page comes next.
- Only continue directly from a non-home landing page when you have already verified it is the correct current page in the ordered plan and that its list context, address, and range are already the intended ones for this run.
- After recovery to a stable list or home context, resume the ordered plan from the earliest configured page that is not yet known complete.
