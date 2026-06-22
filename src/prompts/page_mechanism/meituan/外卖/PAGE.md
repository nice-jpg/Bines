# Meituan `外卖` Page

## Canonical Manual Path
- Current page: `meituan/外卖`.
- After entering any merchant detail page from this list, use `meituan/外卖/商家` as the next manual path.
- Do not use a concrete merchant name in the manual path; merchant names are collection data, not manual paths.

## Structure
- The page is usually divided from top to bottom into a search box, a service icon grid, and a merchant list.
- After filters are confirmed, the merchant list is the primary collection area.

## Operation Logic
- Adjust city, address, and range before bulk collection. Do not start scanning until the configured range is confirmed to be active.
- Start each scan round by reading XML. Identify current-screen merchants, distances, titles, icon or avatar positions, clickable regions, and already-collected status.
- If XML does not provide enough visible-page detail, use `screenshot` before deciding the next operation.
- Prefer swiping within the merchant list area, not the search box or service icon grid.
- If a merchant distance is within range, enter the merchant. Merchants without distance information must not be discarded immediately; enter the merchant detail page and try to recover the distance.
- After all processable merchants on the current screen are handled, call `swipe_up` to load the next screen.
- Do not return just because the first screen has few merchants, the current screen has no in-range merchants, the current screen has no new merchants, or an out-of-range merchant appears.
- End the merchant list only after 2 consecutive `swipe_up` attempts add no new XML or merchant set, or the page clearly shows a no-more/bottom marker.

## Merchant Card Click Logic
- Merchant cards are complex, and different card regions may trigger different actions.
- Each clickable response is usually tied to nearby text.
- If the only goal is to enter the merchant detail page, prefer tapping the merchant icon, merchant avatar, or merchant title.
- Do not tap coupon, delivery, campaign, product preview, favorite, or review areas that may open another page.
