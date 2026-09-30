{"required_aliases":["1","2","3","4"]}

# Meituan takeout 美食 — merchant-list localization guide

## Canonical page
A vertical stack inside the Meituan takeout channel (page title 美食): a red header band holding a
back control and the centred title, a rounded search field reading "请输入商家或商品名称", a
promoted card that heads the scrolling list, a horizontal category chip row, a sort/filter control
row, a row of quick filter chips, and then the scrolling vertical merchant list. Two floating
bubbles (a promotion bubble and a 购物车 bubble) are pinned at the right edge on top of the list.

Only the list head shows the promoted card and the full control band; deeper scroll positions keep
the category chip row, the sort/filter band and the merchant cards, while the promoted card scrolls
away and the sort controls may collapse out of view. Every alias below is chosen so that it stays
locatable in any scroll position.

## Requested fields
The requested schema for this canonical page is
{"collect": false, "note": "list page requires no field collection"}.
This screen only selects, sorts and opens merchant cards; it holds no product-level fields. Merchant
name, score, sales, delivery fee, distance and promotion text are visible, but they describe the
navigation targets (the merchant entries), not fields requested for this page. Do not emit merchant
or product items for this page.

## Required alias roles (numeric string aliases)
- "1" — merchant card list container: the single region whose children are the merchant cards, in
  top-to-bottom list order. Near the list head this region also carries the sort/filter row as its
  first child; further down it holds cards only. It is the vertical scrolling host of the list.
- "2" — category chip row: one horizontal list of equal-width chips whose leading chip is 全部
  (followed by category names such as 饺子馄饨, 汉堡薯条, 意面披萨, 包子粥店, 快餐便当). It is the
  first band under the search field once the promoted card has scrolled away.
- "3" — sort/filter control row: one row holding 综合排序, 速度 and 全部筛选 together with the quick
  filter chips 15分钟, 堂食店, 神券商家, 新客立减. It is a single container region; the sort controls
  and the chips are its children.
- "4" — first merchant card: the first merchant-card child of "1" in list order. It is the card that
  owns the topmost title row of the list; at deep scroll positions it can be clipped by the top edge
  of the viewport, which does not change its role.

## Optional alias
- "5" — promoted card heading the list: the block that owns the 广告 marker together with an 进店
  button and product thumbnails carrying struck-through prices. It exists only while the head of the
  list is in view, so it is not a required alias; assign it when visible.

## Relative geometry and hierarchy
- Chip rows and control rows are wide, short horizontal bands spanning the page width; merchant cards
  are several times taller, with a left image column and a right text column that begins at roughly
  one third of the page width.
- Inside "1", merchant cards follow the visible list order top to bottom. Preserve that order; never
  reorder by score or promotion strength.
- The category chip row, the promoted card and the search field always appear above the merchant
  cards; the sort/filter band sits between the category chips and the cards.

## Ambiguous lookalikes
- Promoted card vs merchant card: both show a merchant name, a score and monthly sales. The promoted
  card is the one carrying the 广告 marker, an 进店 call-to-action and product thumbnails with
  struck-through prices; a merchant card instead carries a minimum-order and delivery row, a distance
  value and a promotion row. Never treat the promoted card as a merchant card of "1".
- Promoted rows can also appear interleaved between merchant cards inside "1". They carry the same
  广告 marker and are not merchant cards, so they never satisfy alias "4".
- Category chip row vs quick filter chips: both are horizontal chip rows. The category row is the one
  that starts with 全部 and sits directly under the search field / promoted card; the quick filters
  belong to the sort/filter container "3" and sit below the sort controls.
- Duplicated chip text: each chip and each sort control exposes the same string twice (a clickable
  node and a text/description node). Count every control once.
- Long-press action menus (撤销, 商家已经被置底, 服务差, 口味不好, 距离太远, 拉黑) are attached inside
  merchant card regions even when no menu is open; they are actions, not card content.
- The floating promotion bubble and the 购物车 bubble overlay the list; they are chrome, not merchant
  content.

## Stability and when to rewrite
Scrolling to any list position, changing sort, or changing filters keeps aliases "1"–"4" on the same
roles; only the merchant cards inside "1" change, and the optional "5" appears or disappears with the
list head. Rewrite this guide if the search field, promoted card, category chip row or sort/filter
band changes order or disappears, if the category row loses its leading 全部 chip, if the region that
directly contains the merchant cards changes shape (for example cards nested one level deeper), or if
the requested schema stops declaring collect=false.
