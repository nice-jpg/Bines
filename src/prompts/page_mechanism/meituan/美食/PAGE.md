---
page_strategy: general
---

# Meituan `美食` Page

## Canonical Manual Path
- Current page: `meituan/美食`.
- After entering any merchant detail page from this list, use `meituan/美食/商家` as the next manual path.
- Do not use a concrete merchant name in the manual path; merchant names are collection data, not manual paths.

## Structure
- The page is usually divided from top to bottom into a search box, a service icon grid, and a merchant list.
- Merchant cards usually include title/avatar/icon areas, distance or location text, rating/review snippets, promotion labels, and product previews.
- A persistent location-service reminder may coexist with a readable merchant list.

## Collection Target
- Merchant fields: merchant name, rating, distance.
- Merchant cards can shift after scrolling; extraction should associate nearby title, rating, and distance fragments within the same visible card.

## Output Schema
```json
{
  "merchants": [
    {
      "name": "",
      "rating": "",
      "distance": ""
    }
  ]
}
```

## Merchant Card Click Logic
- Merchant cards have multiple tap zones. To enter the merchant, prefer the icon, avatar, or title.
- Do not tap coupon, delivery, campaign, product preview, favorite, or review areas that may open another page.
