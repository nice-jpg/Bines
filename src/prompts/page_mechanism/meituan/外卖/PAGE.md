# Meituan `外卖` Page

## Canonical Manual Path
- Current page: `meituan/外卖`.
- After entering any merchant detail page from this list, use `meituan/外卖/商家` as the next manual path.
- Do not use a concrete merchant name in the manual path; merchant names are collection data, not manual paths.

## Structure
- The page is usually divided from top to bottom into a search box, a service icon grid, and a merchant list.
- The page may show transient overlays such as address selectors, location warnings, recommendation banners, or onboarding cards.
- After filters are active and overlays are cleared, the merchant list is the collection area.

## Operation Logic
- Confirm city, address, and range before scanning merchants.
- If an address selector appears and one visible option matches the configured address, choose it immediately instead of cancelling or backing out.
- If nonessential overlays block the list, close them first, then continue on the same `外卖` page rather than ending the task.
- In each round, read XML, identify visible merchants, distance text, title/icon tap targets, and whether each merchant was already handled.
- Use screenshot only when XML cannot show the visible merchant card layout.
- Enter merchants within range. Enter merchants with missing distance if distance may be recovered on the detail page.
- After finishing one in-range merchant, always return to the `外卖` list and continue scanning remaining visible merchants before deciding the page is complete.
- Swipe within the merchant list after all visible candidates are handled. Do not stop because one screen has no in-range or new merchants.
- Stop only after 2 consecutive list swipes add no new merchants, or a clear no-more/bottom marker appears.
- Do not treat “successful entry into 外卖” or “successful probe of one merchant” as task completion; completion requires exhausting the list stop condition for in-range merchants.

## Merchant Card Click Logic
- Merchant cards have multiple tap zones. To enter the merchant, prefer the icon, avatar, or title.
- Do not tap coupon, delivery, campaign, product preview, favorite, or review areas that may open another page.
