# Meituan `外卖` Page

## Overview
- Takeout food merchant list page.
- This page is used to browse merchants under the takeout food category and enter eligible merchant detail pages.

## Canonical Manual Path
- Current page: `meituan/外卖/美食`.

## Structure
- The page is usually divided from top to bottom into a search box, category list, filter controls, and a merchant list.
- Before the true merchant list is available, this flow may first show an address chooser, current-location permission state, or a search-address page.
- After address and distance filters are active, visible merchant cards usually include a merchant name area, rating text, distance text, delivery metadata, promotion text, and possible product previews.

## Collection Target
- Merchant fields: merchant name, rating, distance.
- Merchant names may be split across multiple XML nodes or appear beside icons and promotion labels.
- Rating and distance should be associated with the same merchant card even when their screen positions shift after scrolling.

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
