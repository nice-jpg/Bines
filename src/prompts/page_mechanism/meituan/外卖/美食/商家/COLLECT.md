{"required_aliases":["1"]}
# Merchant menu localization

The page has a merchant identity and delivery header, a same-page ordering/reviews/merchant tab bar, and a menu below. The menu has a narrow left category rail and a wide right product list. Assign alias "1" to the right product-list container, not the rail, whole page, promotion carousel, or checkout bar. The appropriate-ordering reminder marks the start of the collection area; header recommendations and advertisements above it are excluded.

The anchored list may include reminders, banners, category headings and repeated clickable product cards. Cards contain a left product image and a right text column with title, sales metadata and currency amounts, plus purchase controls. Banners and headings are not items. Notification/contact instructions are not food products even if styled like a card.

## Fields and order
- name: the full painted card title, including visible phrase spacing and punctuation. Join wrapped title lines within the card. Ignore stock overlays, image badges and promotional chips such as exclusive-offer labels. Never append sales text or borrow from another card.
- current_price: selling digits and decimal form without currency symbol or promotional price labels. Preserve the visible 起 suffix.
- original_price: the same card's crossed-out list amount without currency symbol; empty string if not shown. Split integer and decimal fragments may be exposed in paint order, so use their geometry and the card's own currency row.
- monthly_sales: the count following 月售, preserving a visible + approximation suffix. No prefix.
Return items in visible top-to-bottom order. Missing optional values are empty strings.

## Completeness and occlusion
Title and selling amount must both be completely readable. Exclude cards whose title is clipped above the pane or covered by a pinned heading. A pinned heading can be a plain category label or an illustrated promotional strip; it can remain pinned even while introductory banners occupy the upper screen. Its sibling glyph/image box and padding define the occlusion band. Positive public bounds alone do not prove visibility.

Exclude a card if its selling-price glyph row is clipped by the bottom edge. If only an optional original-price row is clipped, retain the card with original_price empty. Cards clipped only after their complete title and selling price remain collectible. Do not relax completeness for boundary rows.

## Lookalikes and maintenance
The category rail is navigation, not products. In-flow category headings and promotions have no purchasable title/price combination. Savings badges (立省, 已含券), flavor and ingredient tags, purchase minimums, and advertised discounts are not product prices or names. Merchant name, rating and distance are separate merchant metadata, never menu items; use empty string for absent distance.

Rewrite this guide when schema or structural recognition changes, not when merchant, category or product labels change. Reannotate every saved case after a guide change. Safe swipes start inside the right product pane, away from category anchors, purchase controls and checkout. Use the same-page ordering tab's top-reset affordance to restore the page after validation.
