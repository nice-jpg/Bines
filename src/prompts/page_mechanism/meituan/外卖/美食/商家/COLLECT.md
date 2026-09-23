{"required_aliases":["1"]}

# Meituan food-merchant menu localization guide

## Required semantic anchor

Assign alias **"1"** to the main product-feed/list region in the ordering tab. It is the vertically scrolling list on the product side of the menu, located below the centered warm reminder (“温馨提示：请适量点餐”) and, in a two-column layout, to the right of the narrow category rail. The region contains a category heading or promotional banner followed by repeated purchasable product cards.

Do not assign the alias to the outer page switcher, the whole menu scroller, the left category list, a single product card, the warm-reminder text, or a promotion/banner. If the menu uses a single-column or tab-style layout, choose the repeated product-list container immediately following the warm reminder rather than the tabs themselves.

## Product recognition and requested fields

Within alias "1", products are repeated sibling card-like regions. A genuine product normally has a dish/product name and a purchase affordance (such as a yellow plus/add control) and/or a displayed currency price. Product cards can also contain an image, monthly-sales text, specification/flavor tags, and quantity controls. Category headings, recommendation labels, banners, notices, policy text, reminders, coupons, and pure promotional copy are not products.

For each visually present product card, preserve top-to-bottom visual order and extract only text belonging to that card:

- `name`: the prominent product title.
- `monthly_sales`: the complete monthly-sales phrase, normally beginning with “月售”. Keep it as displayed.
- `price`: the current single selling price when only one price is shown. Normalize visual spacing within a split price label (for example a currency symbol, integer, decimal point, and decimal fraction in adjacent descendants) into one readable string while retaining the currency symbol.
- `original_price`: the struck-through/list price when a card visibly presents both an old and a discounted price. Do not infer it from promotions elsewhere.
- `discount_price`: the emphasized current discounted price when both old and discounted prices are visibly present.

When there is no discount pair, put the displayed selling price in `price` and leave `original_price` and `discount_price` empty. When a field is not visible or absent, use the schema’s empty string. If a card is clipped by a viewport edge, include only fields actually visible for that card; never recover hidden fields from unrelated text or another card.

## Geometry and ambiguity rules

Use the alias container’s repeated direct child regions and their descendants to keep fields scoped to the correct product. Product cards occupy the product column and are stacked vertically; the left-side category labels are navigation anchors and must never become names. Ignore text above the first genuine product card when it is a category heading or image promotion. Exclude checkout-bar totals, delivery fees, minimum-order values, merchant-level monthly sales, ratings, and tab counts.

A leaf price may be exposed with spaces between numeric fragments; aggregate those fragments only inside its own product card. Do not combine separate prices from adjacent cards. Visibility is determined by the card/field bounds intersecting the current viewport and by readable visual evidence, not merely by presence somewhere in the accessibility tree.

## When to rewrite this guide

Rewrite only if the page family changes structurally—for example, the purchasable products are no longer represented as repeated cards under the warm reminder, or a new layout requires a different semantic anchor. Do not rewrite it for different merchants, product names, categories, prices, scroll positions, or node IDs.