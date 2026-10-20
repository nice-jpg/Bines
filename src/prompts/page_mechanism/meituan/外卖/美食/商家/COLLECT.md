{"required_aliases":["1"]}
# Menu collection

This page is a Meituan food-delivery merchant detail page. From top to bottom it holds the
merchant identity block (name, rating, delivery metadata, promotion strips), a same-page tab
bar (点菜 / 评价 / 商家), and the ordering pane. The ordering pane is split horizontally: a
narrow category rail on the left lists category names only, while the wide right-hand column
is the vertical product list. Assign the required alias "1" to that right-hand product list
container (the list whose children are the product cards), not to the category rail, the tab
bar, the merchant header or the checkout bar.

## Structure of the annotated list

The list container begins with the appropriate-ordering reminder (温馨提示: 请适量点餐) and
may also contain promotional banners, in-flow category headings (推荐, 地锅区, 凉菜 ...), and
the repeated product cards. All of these are children of the same list, so a card is
recognised by its repeated internal layout rather than by position:

- product image on the left of the card,
- a title text node at the top of the card's text column,
- a sales row beginning with 月售, followed by tag words (approximate volume, flavor,
  ingredients) that are metadata, not identity,
- one or more currency rows holding the selling amount and, when discounted, the
  crossed-out list amount,
- an add-to-cart control, and optional coupon/saving badges such as 已含券, 立省¥2.

Category headings, banners, the reminder line and the rail labels are never items: they carry
no title/price/add-control combination.

In scrolled states the current category heading is also exposed as a sibling of the list that
stays pinned over the pane's top edge. Its glyph box plus padding defines an occlusion band;
a card whose title lies in that band is covered and must be treated as unreadable.

## Requested fields

- name: the complete product title of the card. Titles can wrap onto a second line; join the
  fragments of the same card only. Never borrow words from the sales row, badges, banners or
  neighbouring cards.
- current_price: the selling amount of the card, digits only, no currency symbol, using the
  decimal form actually shown (for example 20.8, 8.8, 1). Drop labels such as 到手价 / 神券价
  and keep an attached 起 suffix only when it is painted as part of the amount.
- original_price: the struck-through list amount of the same card, digits only. It is often
  painted in the same text region as the selling amount (fragments may be exposed in paint
  order rather than reading order), so reconstruct both amounts from that card's own currency
  rows. Use an empty string when the card shows no list amount.
- monthly_sales: the number after 月售 on the same card, digits only.
- Preserve visible top-to-bottom item order. Use empty strings for fields that are not shown.

## Readability and completeness

Decide per card, from geometry inside the pane, never from neighbouring cards or from earlier
states:

- The product title and the selling amount must be readable inside the pane. A card scrolled
  under the pinned heading or cut by the pane's upper edge has no readable title and is
  excluded.
- Currency rows of one role (selling amount, list amount) share a glyph-line height wherever
  they are fully painted. A row cut by the pane's lower edge exposes a visibly shorter strip;
  such an amount is not complete and must not be reported. When the selling row is cut, the
  whole card is excluded; when only the list amount is cut, keep the card and leave
  original_price empty.
- Cards clipped after the title and selling amount (badges or the add control cut by the pane
  edge) stay in the result.

## Ambiguous lookalikes

- Banners and promotional prose can advertise prices without being purchasable cards.
- 到手价, 神券价, 一口价 are price labels; 立省¥N and 已含券 are saving badges, not amounts.
- The monthly-sales row's tag words (约750毫升, 辣, 鸡蛋, 一件即送) are metadata.
- Dataset categories from the left rail (推荐, 人气精选, 本店特色炒鸡, 炒菜, 烧菜, 干锅系列,
  凉菜, 地锅区, 主食, 饮料, 汤类) are navigation, never products.

Rewrite this guide only when the page layout or the requested schema changes - not for merchant
names, product labels, node identifiers or coordinates.
