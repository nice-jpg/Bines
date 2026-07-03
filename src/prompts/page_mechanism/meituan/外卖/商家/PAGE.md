# Meituan `外卖` Merchant Page

## Canonical Manual Path
- Current page: `meituan/外卖/商家`.
- This manual applies to any concrete merchant entered from `meituan/外卖`.
- Do not replace `商家` with a concrete merchant name in query_manual.

## Structure
- The upper area usually contains basic merchant information and may hide after scrolling.
- The middle area usually contains tab titles.
- The lower area is usually split into a left category list and a right product list for the selected category.
- The left category list may scroll independently. The right product list is the product collection area.

## Required collection priority
1. First lock the merchant fields that are actually required for output: merchant name, distance, rating, and total review count.
2. Then scan the right-side product list category by category until the reachable product area reaches a stop condition.
3. Only after the merchant page is complete should you write the merchant worksheet and matching `Sheet1` row, then return to the list.

## Operation Logic
- Verify this is the expected merchant detail page. If it is an activity, coupon, product, ad, or unrelated page, return to the list.
- Collect only the merchant fields needed by the workbook contract: name, distance, rating, and total review count. Do not spend extra swipes chasing merchant metrics that are not written to Excel, such as merchant sales or positive-review counts.
- Collect every reachable product: product name, price, original price, discount price, and monthly sales.
 
 ## Non-product item exclusion
 Product collection must include only real purchasable food, drink, or goods items. Skip items that are purely informational — instructions, warnings, reminders, policies, or notices that are not actual products. Examples of items to exclude:
 - Instruction or warning notices: "默认有一份免费餐具...", "本身带打包盒，慎拍", "一人节约，全家光荣", "光盘行动，从我做起", "配送说明", "制作说明", "给个满意呗", "饭量大的朋友可以备注...", "小店诚信经营..."
 - Placeholder items with price 0 and no meaningful sales that function as notices.
 - Items whose names describe a policy, instruction, or reminder rather than a food or drink.
 - Decorative or meta items labeled as "温馨提示" or containing only delivery/policy text.
 To decide: read the item name. If it describes a food, drink, bundle, or condiment and has a price >0 or genuine sales, include it. If the name is purely a notice/instruction and price is 0 or missing, skip it. When uncertain, inspect the card fully before deciding.
- Use product sales = `0` only when the product's sales value is truly missing after inspection.
- Do not use `0` as a fallback for missing price. If price is not yet visible, re-center the card or inspect again; leave price blank only if the page still does not expose it.
- If a visible product row is cut off, missing a price, or missing a sales value because the card is only partially on screen, re-center it and inspect again before deciding the row is complete.
- Scan the right product list, tracking seen products. Swipe inside the right product list, not the left category list.
- When the current category is exhausted, move to the next visible left-side category and continue the same product scan.
- Count a swipe as productive only if it reveals at least one new fully inspected product card. Repeated cards or non-product content count as a no-new-product swipe.
 
 ## Thorough product scanning
 The most frequent failure is incomplete scanning — collecting only 2-4 products from a merchant that has 20+ reachable products. Avoid this by following the procedure exactly:
 1. Start with the currently selected left-side category. Swipe inside the right product list (use y=2000 to y=2100, never y=2400 or near y=0) to reveal all products in that category.
 2. When no new products appear after 2 consecutive swipes, tap the NEXT category in the left sidebar. The left category list itself may need scrolling.
 3. Repeat: swipe the right product list for each category. Do not skip any category that shows a label (categories like "温馨提示" that contain only notices — skip those, but check first).
 4. After every category has been visited and its products recorded, only then write the merchant's data.
 5. If you have written fewer than 10 products and the merchant visibly has multiple category tabs, you have not scanned thoroughly. Re-enter the merchant and continue.
 
 Do not write a merchant's product sheet until you can account for every reachable product across all its categories. A merchant with many category tabs always has more products than the first few visible cards.
- Finish product collection only after 2 consecutive product-list swipes add no new products for the current reachable area, or a clear product-list terminal marker appears.
- Do not leave this merchant until all required merchant output fields are resolved and all reachable products are recorded.
- Before deciding the merchant is complete, perform this self-check:
  1. merchant name, distance, rating, and total review count have been actively inspected;
  2. every visible product card on the current and previous swipe windows has been inspected;
  3. no row has an obviously truncated or borrowed price;
  4. the rows are valid to serialize to Excel.
- Once the merchant page reaches its stop condition, write the merchant worksheet and the matching `Sheet1` row immediately, then return to the list.
- If you are still inside a merchant page and are not blocked by captcha, popup, or tool failure, continue on-device collection rather than replying with a progress summary.
- Write all collected rows to the `result.xlsx` worksheet named with this merchant's complete name before using `swipe_back` to return to the list.

## Price field mapping rules
Product cards may show multiple prices. Map them to the four output fields as follows:

- **price**: The standalone menu price, usually displayed as a larger `¥XX` or the main price next to the product name. This is the default price the product is sold at. Do not use a smaller cross-out/through price or a "到手价" label for this field.
- **original price** (原价): A higher price shown crossed out, struck through, or labeled as "原价¥XX". If no struck-through or "原价" price exists, leave this field empty (`""`).
- **discount price** (折扣价/到手价): A special promotional price displayed as the smaller "到手价¥XX", "折扣价¥XX", "神券价¥XX", or marked with a promotion tag. If the product card shows only one price (no struck-through price, no 到手价 label), leave discount price empty.
- **monthly sales**: The text "月售XX" or "已售XX". Normalize: "月售200+" → 200, "月售1.7万+" → 17000.

**Common example**: A product card shows `¥29.9 ¥39.9 月售100+` with "到手价" tag. Here: price=29.9, original_price=39.9, discount_price=29.9, monthly_sales=100.

**Another example**: A product card shows `¥4.9` and a smaller `到手价￥1.6`. Here: price=4.9, original_price="", discount_price=1.6, monthly_sales=69.

**Another example**: A product card shows only `¥25.9 月售700+`. Here: price=25.9, original_price="", discount_price="", monthly_sales=700.

**Important**: Do not put the 到手价 value into the original_price column. The original_price is the struck-through or "原价" price, not the promotional discounted price.
