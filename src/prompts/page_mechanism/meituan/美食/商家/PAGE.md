# Meituan `美食` Merchant Page

## Canonical Manual Path
- Current page: `meituan/美食/商家`.
- This manual applies to any concrete merchant entered from `meituan/美食`.
- Do not replace `商家` with a concrete merchant name in page paths.

## Structure
- The upper area usually contains basic merchant information and may hide after scrolling.
- The middle area usually contains tab titles such as `团购`, `菜品`, `评价`, `更多推荐`.
- The lower area may be either:
  - a true left-category + right-product layout, or
  - a single vertically scrolling recommendation / dish list where each visible dish card already counts as a product record.
- Product cards may show the price as a standalone `¥xx`, as an inline text fragment such as `19.82元 套餐名`, or may temporarily hide price until the card is fully visible.
- Sales may appear as a plain number near the right side of the card, not necessarily with a label.

## Collection Target
- Merchant fields: merchant name, distance, rating, total review count.
- Product fields: product name, price, sales.
- Product data may appear in `菜品`, `团购`, `优惠`, `推荐`, `套餐`, or similar product-relevant tabs.
- Names, prices, and sales may be split across sibling XML nodes; extraction should group text by repeated product-card structure instead of one fixed coordinate.

## Output Schema
```json
{
  "merchant": {
    "name": "",
    "distance": "",
    "rating": "",
    "review_count": ""
  },
  "items": [
    {
      "name": "",
      "price": "",
      "sales": ""
    }
  ]
}
```
