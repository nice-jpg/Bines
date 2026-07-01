# Meituan `美食` Page

## Canonical Manual Path
- Current page: `meituan/美食`.
- After entering any merchant detail page from this list, use `meituan/美食/商家` as the next manual path.
- Do not use a concrete merchant name in the manual path; merchant names are collection data, not manual paths.

## Structure
- The page is usually divided from top to bottom into a search box, a service icon grid, and a merchant list.
- After filters are active, the merchant list is the collection area.
- The search box may retain a keyword after returning from a merchant. A filled search box filters the list to only merchants matching that keyword.

## Operation Logic
- Confirm city, address, and range before scanning merchants.
- In each round, read XML, identify visible merchants, distance text, title/icon tap targets, and whether each merchant was already handled.
- Use screenshot only when XML cannot show the visible merchant card layout.
- Enter merchants within range. Enter merchants with missing distance if distance may be recovered on the detail page.
- After tapping a merchant from the `美食` list, treat the transition as unfinished until you have either queried `meituan/美食/商家` or inspected the destination page and continued the merchant workflow. Do not reply to the user from the list-to-merchant transition.

## Post-Return Recovery Procedure
After `swipe_back` returns from a merchant detail page to the `美食` list, you **must** run this recovery before deciding the next action:

1. **Check the search box**: Read the XML to see whether the search box contains non-empty text or a search keyword. If the search box has an unexpected keyword (e.g., a merchant name from an earlier entry), the list is filtered and will not show all merchants.
2. **Clear the search keyword**: If a stale keyword exists, clear it by tapping the clear/delete button inside the search box (usually a small "✕" icon at the right end of the search box), or by tapping a nearby "取消" (cancel) button, or by tapping the search box and looking for a clear option. Do NOT navigate away from the 美食 page for this step.
3. **Re-apply the distance filter**: After the search keyword is cleared, check whether the 1km (or configured range) distance filter is still active. If it is not, tap the distance filter button to re-apply it before scanning merchants.
4. **Re-scan the list**: Only after the search keyword is cleared and the distance filter is confirmed active should you read the XML again to identify all visible in-range merchants.
5. If the search box cannot be cleared from the `美食` page itself (no clear button, no cancel action), then and only then go back to home and re-enter `美食` by tapping its entry icon, then immediately re-apply the distance filter and re-scan.

**Important**: Skipping the search-box check is a common mistake. Even if the search box looks empty visually, always verify via XML before concluding the list is unfiltered.

## List Continuation Rules
- After returning from one merchant and clearing any stale search keyword, immediately resume the `美食` list workflow: refresh the list state, identify the remaining visible unhandled in-range merchants, and continue with the next merchant or the next required list swipe.
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
- Tapping a merchant's name or icon area is the safest way to enter its detail page without triggering a search keyword or opening non-merchant pages.
