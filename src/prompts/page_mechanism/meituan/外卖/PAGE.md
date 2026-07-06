# Meituan `外卖` Page

## Canonical Manual Path
- Current page: `meituan/外卖`.
- After entering any merchant detail page from this list, use `meituan/外卖/商家` as the next manual path.
- Do not use a concrete merchant name in the manual path; merchant names are collection data, not manual paths.

## Structure
- The page is usually divided from top to bottom into an address area, a search box, filter controls, and a merchant list.
- Before the true merchant list is available, this flow may first show an address chooser, current-location permission state, or a search-address page.
- After address and distance filters are active, the merchant list is the collection area.

## Operation Logic
- Confirm city, address, and range before scanning merchants.
- If the first `外卖` entry opens an address chooser instead of the merchant list, treat that chooser as part of the `meituan/外卖` flow, not as an abnormal page.
- On an address chooser/search page, first inspect XML and screenshot carefully for existing tappable address candidates, recent addresses, current configured address text, a search box, confirm buttons, or clear-entry actions.
- Prefer reusing an already visible candidate that matches or closely matches the configured address over assuming text input is required.
- If a search box is present but no text-input tool exists, still inspect for preset suggestions, recent-history addresses, map pins, nearby communities/buildings, or a confirm-current-location action that can reach the configured area without typing.
 - Only conclude that address setup is blocked after you have inspected both XML and screenshot and verified there is no tappable path to select or confirm a suitable address.
 - **Critical: Confirm exact address match.** Before treating address setup as complete, verify the selected or confirmed address matches the **configured address character by character** (including regional spelling variants). If the visible address differs from the configured address (e.g., a different character, extra room/building number, or truncated name), do not proceed with merchant scanning. Instead, inspect additional address candidates, try search box suggestions, or tap nearby candidates that more closely match the configured address. Only proceed when you have confirmed the active address equals or unambiguously contains the configured address text.
 - After address confirmation succeeds, continue to the merchant list, then read XML, identify visible merchants, distance text, title/icon tap targets, and whether each merchant was already handled.
- Use screenshot when XML cannot show the visible merchant card layout or the address chooser contents reliably.
- Enter merchants within range. Enter merchants with missing distance if distance may be recovered on the detail page.
- Swipe within the merchant list after all visible candidates are handled. Do not stop because one screen has no in-range or new merchants.
- Stop only after 2 consecutive list swipes add no new merchants, or a clear no-more/bottom marker appears.

## Merchant Card Click Logic
- Merchant cards have multiple tap zones. To enter the merchant, prefer the icon, avatar, or title.
- Do not tap coupon, delivery, campaign, product preview, favorite, or review areas that may open another page.

## Blocked-state rule
- Do not summarize the run from the first address chooser or first failure message.
- If one address-selection attempt fails, recover and try another visible candidate or confirmation path before declaring the `外卖` page blocked.
