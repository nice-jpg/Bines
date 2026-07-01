# Meituan `美食` Merchant Page

## Canonical Manual Path
- Current page: `meituan/美食/商家`.
- This manual applies to any concrete merchant entered from `meituan/美食`.
- Do not replace `商家` with a concrete merchant name in query_manual.

## Structure
- The upper area usually contains basic merchant information and may hide after scrolling.
- The middle area usually contains tab titles such as `团购`, `菜品`, `评价`, `更多推荐`.
- The lower area may be either:
  - a true left-category + right-product layout, or
  - a single vertically scrolling recommendation / dish list where each visible dish card already counts as a product record.
- Product cards may show the price as a standalone `¥xx`, as an inline text fragment such as `19.82元 套餐名`, or may temporarily hide price until the card is fully visible.
- Sales may appear as a plain number near the right side of the card, not necessarily with a label.

## Required collection priority
1. First lock the merchant fields that are actually required for output: merchant name, distance, rating, and total review count.
2. Then switch into the most product-complete area, preferring `菜品` over `团购` or `推荐` when available.
3. Only write Excel after product collection has reached a stop condition and a final row-quality check has passed.

## Operation Logic
- Verify this is the expected merchant detail page. If it is an activity, coupon, product, ad, or unrelated page, return to the list.
- Collect only the merchant fields needed by the workbook contract: name, distance, rating, and total review count. Do not spend extra swipes chasing merchant metrics that are not written to Excel, such as merchant sales or positive-review counts.
- Treat a visible dish card as one product. For each product, extract exactly one row with product name, price, and sales.
- Use product sales = `0` only when the product's sales value is truly missing after inspection.
- Do not use `0` as a fallback for missing price. If price is not yet visible, keep scrolling or re-center the card first; only leave price blank if the page still does not expose it.
- When a card contains multiple inline offer texts or combo descriptions, do not copy those offer texts as the product price unless they are the only visible price tied to that specific product. Prefer a standalone nearby `¥` price for the product card.
- Scan the product list carefully, tracking seen product names to avoid duplicates.
- Count a swipe as productive only if it reveals at least one new fully inspected product card. If a swipe reaches review content, recommendation content without new unique products, or repeated cards, count that as a no-new-product swipe.
- Continue swiping the product list until 2 consecutive product-list swipes add no new fully inspected products, or a clear terminal marker appears.
- Do not write the merchant's Excel rows early just because some products are already visible. Finish the merchant inspection first, then write once.
- If a visible product row is cut off, missing a price, or missing a sales value because the card is only partially on screen, re-center it and inspect again before deciding the row is complete.
- If a create/write call fails, treat the merchant as unfinished: diagnose the bad row data or formatting, repair it, and retry the write before leaving the merchant page.
- Before deciding a merchant is complete, perform this self-check in order:
  1. every required merchant output field has been actively inspected;
  2. every visible product card on the current and previous swipe windows has been inspected for name, price, and sales;
  3. no row has an obviously borrowed price, truncated value, or malformed structure;
  4. the rows to be written are valid and complete enough to serialize cleanly.
- Do not leave this merchant, write an empty product sheet, or write placeholder merchant values just to keep progress moving. If products or required merchant output fields are still recoverable from the current page by more inspection, keep inspecting.
- Once the reachable product area meets the stop condition, write the merchant worksheet and the matching `Sheet1` row immediately, then return to the list. Do not pause on the merchant page to ask the user whether to continue.
- If you are still inside a merchant page and are not blocked by captcha, popup, or tool failure, continue on-device collection rather than replying with a progress summary.
- Write all collected rows to the `result.xlsx` worksheet named with this merchant's complete name before using `swipe_back` to return to the list.
