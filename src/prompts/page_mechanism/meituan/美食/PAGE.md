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
- After tapping a merchant from the `美食` list, treat the transition as unfinished until you have either queried `meituan/美食/商家` or inspected the destination page and continued the merchant workflow. Do not reply to the user from the list-to-merchant transition.
- After returning from one merchant, immediately resume the `美食` list workflow: refresh the list state, identify the remaining visible unhandled in-range merchants, and continue with the next merchant or the next required list swipe.
- The first decision after returning from a merchant must be a list-continuation decision, not a stop/summarize decision. From the `美食` list, your next step should be one of: inspect the list, enter the next eligible merchant, or swipe the list to reveal more merchants.
- Swipe within the merchant list after all visible candidates are handled. Do not stop because one screen has no in-range or new merchants.
- Treat `美食` as incomplete until the list itself reaches a stop condition. Completing one merchant, creating Excel sheets, attempting another page, or writing a progress summary is not a valid stopping point.
- The ordering rule is strict: finish the `美食` list first, then navigate to the separate configured page `外卖`. Do not leave `美食` early just to begin `外卖` while `美食` still has untested visible merchants or pending list swipes.
- Only after the `美食` list reaches its own stop condition should you return or navigate onward to `外卖`.
- Do not end the run, hand control back, or produce a natural-language progress/final summary while `美食` still has untested visible merchants, while additional list swipes are still required, or before the separate configured page `外卖` has also been attempted.
- If you have just returned to the `美食` list and are not blocked by captcha, a popup, or a tool error, continue operating on-device instead of replying to the user.
- A persistent location-service reminder on the `美食` list is not by itself a blocking popup. If the merchant list is visible, distances or merchant cards can still be inspected, or the configured address/range can still be checked, continue the list workflow instead of handing control back to the user.
- Escalate the `美食` page to the user for location help only when you have verified that the location state prevents reaching or reading the merchant list itself, prevents applying the required address/range after inspection attempts, or causes repeated navigation failure that leaves no on-device path to continue.
- Before declaring a location-related block, first inspect the current list state and attempt the next normal list action that is still available on-device, such as checking visible merchants, opening the next eligible merchant, or swiping for more merchants.
- Stop the `美食` list only after 2 consecutive list swipes add no new merchants, or a clear no-more/bottom marker appears.

## Merchant Card Click Logic
- Merchant cards have multiple tap zones. To enter the merchant, prefer the icon, avatar, or title.
- Do not tap coupon, delivery, campaign, product preview, favorite, or review areas that may open another page.
