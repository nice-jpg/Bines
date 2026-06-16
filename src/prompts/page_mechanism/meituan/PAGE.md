# Meituan Home Page

## Structure
- The home page contains entry points for secondary pages such as `美食` and `外卖`.
- Page content may be represented imperfectly in UIAutomator XML, so use screenshots when XML and visible layout are hard to match.

## Operation Logic
- Use the configured secondary page names from `<config_context>` as the target entry labels.
- Directly find and tap the configured secondary page entry; do not detour into unrelated entries.
- Before tapping, confirm the entry is visible and not covered. If needed, swipe to bring it fully into view.
- After tapping, verify that the destination page matches the selected secondary page. If it clearly does not, use `swipe_back` and retry from the home page.
