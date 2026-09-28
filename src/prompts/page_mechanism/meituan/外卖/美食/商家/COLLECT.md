{"required_aliases":["1"]}

# Meituan food-merchant menu localization guide

## Required semantic anchor

Assign alias **"1"** to the smallest exposed region containing the visible, vertically repeated product feed beneath the ordering tab. In the common two-column layout this is the product list on the right of the category rail. In a single-column or tab-style layout, choose the repeated product-list container. If no dedicated list is exposed, use the smallest outer rendered-surface region that contains all visible product cards.

Do not anchor the merchant header, ordering-tab bar, category rail, a category heading, a single product card, a banner, reminder, or checkout bar.

## Product recognition and requested fields

A genuine product is a repeated card/block with a prominent dish title and a displayed selling price and/or purchase affordance such as a yellow add or specification button. A card may also have an image, monthly-sales text, tags, recommendations, voucher text, and an original struck-through price. Category headings, navigation labels, recommendation banners, duplicate-fold notices, policies, coupons by themselves, reminders, reviews, and promotional prose are not products.

For every visually present product card, preserve top-to-bottom order and extract only these fields from that same card:

- `name`: the prominent product title.
- `current_price`: the currently payable displayed price. Keep the currency symbol and decimal as shown. Retain an immediately attached starting-price suffix such as “起”, but omit separate offer labels such as “神券价”.
- `original_price`: the struck-through/list price when shown alongside a discounted current price; otherwise an empty string.
- `monthly_sales`: the complete visible monthly-sales phrase beginning with “月售”, such as “月售100+”.

Use an empty string for an absent or unreadable field. Include a viewport-edge card only when its complete product name is readable; never recover clipped fields from another state, another card, or unrelated text.

## Geometry and ambiguity rules

Scope all fields to one product card. Product cards stack vertically in the product column; category labels in the left rail are never products. A category title may appear between groups but lacks its own price/purchase affordance. Ignore merchant-level sales, rating, delivery, checkout totals, fees, minimum-order amounts, crossed-out savings in unrelated banners, and promotional rankings.

Prices can be split into visual text fragments; combine only adjacent currency and numeric fragments within the same card into one readable value. A gray struck-through price is `original_price`, while the emphasized red price is `current_price`. Determine inclusion and completeness from the screenshot’s readable viewport, not bounds alone.

## When to rewrite this guide

Rewrite only when this page family’s structure or requested schema changes—for example, products cease to be repeated menu cards or a materially new layout needs another semantic anchor. Do not rewrite it for different merchants, products, categories, prices, scroll positions, node IDs, or coordinates.