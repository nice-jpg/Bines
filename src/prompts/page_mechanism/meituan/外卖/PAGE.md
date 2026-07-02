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
- After address selection succeeds, continue to the merchant list, then read XML, identify visible merchants, distance text, title/icon tap targets.

## Merchant Capture & Collection Workflow

### Phase 1: Full list scan and merchant capture
- **Before entering any merchant**, first perform a complete scan of the merchant list.
- Swipe through the entire list until the list stop condition is reached (2 consecutive swipes add no new merchants, or a clear bottom marker appears).
- During this scan, use `think` to record every discovered merchant that is within range. For each, record: merchant name, distance, rating (if visible on the list card), and its approximate tap position.
- After the full scan is complete, use `think` to enumerate the captured capture list: the complete set of in-range merchants that must be collected, with their names and distances.

### Phase 2: Systematic merchant collection
- Enter merchants one by one from the captured capture list.
- Before entering each merchant, use `think` to state which merchant you are entering and which merchants remain.
- After each merchant's data is written to Excel and you return to the list, use `think` to confirm which captured merchants have been collected and which remain.
- If a captured merchant from the original scan is not immediately visible when you return to the list, do not skip it — scroll to find it. The list may have re-sorted. Look for the merchant name, not the old position.
- Continue until every merchant from the captured capture list has been entered and collected, or until you have spent at least 6 consecutive swipes looking for a remaining merchant without finding it.

### Swipe and stop rules
- Swipe within the merchant list after all visible candidates are handled on the current viewport.
- Do not stop because one screen has no in-range or new merchants.
- Stop the list scan only after 2 consecutive list swipes add no new merchants, or a clear no-more/bottom marker appears.

## Merchant Card Click Logic
- Merchant cards have multiple tap zones. To enter the merchant, prefer the icon, avatar, or title.
- Do not tap coupon, delivery, campaign, product preview, favorite, or review areas that may open another page.

## Blocked-state rule
- Do not summarize the run from the first address chooser or first failure message.
- If one address-selection attempt fails, recover and try another visible candidate or confirmation path before declaring the `外卖` page blocked.
