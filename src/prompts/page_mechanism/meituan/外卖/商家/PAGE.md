# Meituan `外卖` Merchant Page

## Structure
- The upper area usually contains basic merchant information and may hide after scrolling.
- The middle area usually contains tab titles.
- The lower area is usually split into a left category list and a right product list for the selected category.
- The left category list may scroll independently; the right product list is the target area for product collection.

## Operation Logic
- After entering a merchant, verify that the page is the expected merchant detail page. If it is an activity, coupon, product, ad, or unrelated page, use `swipe_back` to return to the list and continue.
- Collect merchant name, rating, sales information, and distance information.
- Collect review information, including total review count and positive review count. Scroll or expand the review area or review page until these counts can be obtained.
- Run a scrolling scan over the right-side product list. In each round, read XML, track already-seen products, and collect product name, price, and sales.
- Record product sales as 0 when missing.
- Use `swipe_up` inside the right-side product list area to load more products. Do not mistake scrolling the left-side category list for paging through products.
- End product collection only after 2 consecutive `swipe_up` attempts add no new products, or a product-list terminal marker appears.
- After all products for the merchant have been recorded, use `swipe_back` to return to the previous page.
