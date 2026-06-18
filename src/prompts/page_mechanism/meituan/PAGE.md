# Meituan Home Page

## Canonical Manual Path
- Current page: `meituan`.
- Next secondary pages use their configured page type paths, for example `meituan/美食` and `meituan/外卖`.
- Do not query `meituan/首页` or `美团/首页`; the home manual path is the app path itself.

## Structure
- The home page contains entry points for secondary pages such as `美食` and `外卖`.
- Page content may be represented imperfectly in UIAutomator XML, so use screenshots when XML and visible layout are hard to match.

## Operation Logic
- Use the configured secondary page names from `<config_context>` as the target entry labels.
- Directly find and tap the configured secondary page entry; do not detour into unrelated entries.
- Before tapping, confirm the entry is visible and not covered. If needed, swipe to bring it fully into view.
- After tapping, verify that the destination page matches the selected secondary page. If it clearly does not, use `swipe_back` and retry from the home page.
