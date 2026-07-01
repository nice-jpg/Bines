# Meituan `美食` Page

## Canonical Manual Path
- Current page: `meituan/美食`.
- After entering any merchant detail page from this list, use `meituan/美食/商家` as the next manual path.
- Do not use a concrete merchant name in the manual path; merchant names are collection data, not manual paths.

## Structure
- The page is usually divided from top to bottom into a search box, a service icon grid, and a merchant list.
- After filters are active, the merchant list is the collection area.
- Top search/location UI may show a current place string, but tapping it can open a generic search page instead of a reliable address-confirmation flow.

## Operation Logic
- Treat city/address/range confirmation as a prerequisite check, not an automatic blocker.
- If city and range are already consistent with the task, first look for a clearly labeled, stable address-selection or location-confirmation entry.
- Do not tap the top search box or generic keyword search field as an address-setting attempt unless the UI explicitly indicates address/location management.
- If no stable address-setting entry is visible within a brief inspection, continue merchant collection from the current list instead of stopping the whole task for manual address changes.
- During collection, record in reasoning/user notices that the visible place string may differ from the configured address when it could not be safely changed.
- In each round, read XML, identify visible merchants, distance text, title/icon tap targets, and whether each merchant was already handled.
- Use screenshot only when XML cannot show the visible merchant card layout.
- Enter merchants within range. Enter merchants with missing distance if distance may be recovered on the detail page.
- Swipe within the merchant list after all visible candidates are handled. Do not stop because one screen has no in-range or new merchants.
- Stop only after 2 consecutive list swipes add no new merchants, or a clear no-more/bottom marker appears.

## Merchant Card Click Logic
- Merchant cards have multiple tap zones. To enter the merchant, prefer the icon, avatar, or title.
- Do not tap coupon, delivery, campaign, product preview, favorite, or review areas that may open another page.
