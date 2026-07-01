# Meituan `美食` Page

## Canonical Manual Path
- Current page: `meituan/美食`.
- After entering any merchant detail page from this list, use `meituan/美食/商家` as the next manual path.
- Do not use a concrete merchant name in the manual path; merchant names are collection data, not manual paths.

## Structure
- The page is usually divided from top to bottom into a search box, a service icon grid, and a merchant list.
- After filters are active, the merchant list is the collection area.

## Operation Logic
- Confirm city, address, and range before scanning merchants.
- In each round, read XML, identify visible merchants, distance text, title/icon tap targets, and whether each merchant was already handled.
- Use screenshot only when XML cannot show the visible merchant card layout.
- Enter merchants within range. Enter merchants with missing distance if distance may be recovered on the detail page.
- After returning from one merchant, immediately resume the `美食` list workflow and continue handling the remaining visible or newly revealed in-range merchants.
- Swipe within the merchant list after all visible candidates are handled. Do not stop because one screen has no in-range or new merchants.
- Treat `美食` as incomplete until the list itself reaches a stop condition. Completing one merchant, creating Excel sheets, or writing a progress summary is not a valid stopping point.
- Do not end the run, hand control back, or produce a final/progress summary while `美食` still has untested visible merchants, while additional list swipes are still required, or before the separate configured page `外卖` has also been attempted.
- Stop the `美食` list only after 2 consecutive list swipes add no new merchants, or a clear no-more/bottom marker appears.

## Merchant Card Click Logic
- Merchant cards have multiple tap zones. To enter the merchant, prefer the icon, avatar, or title.
- Do not tap coupon, delivery, campaign, product preview, favorite, or review areas that may open another page.
