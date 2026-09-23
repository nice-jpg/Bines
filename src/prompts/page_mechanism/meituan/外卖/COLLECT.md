{"required_aliases":["1"]}
# Collection guide

## Scope and output

The merchant and collection schemas request no fields. For every supported same-page state, return exactly the empty JSON object `{}`. Do not extract, retain, or expose any visible address, contact, merchant, category, or search text because none is requested.

## Page recognition and structure

This route may show either the delivery-address setup flow or the food-delivery listing flow. The address flow is typically organized vertically as a title/header, city or location controls, a search field, a location-status row, and saved-address rows. The listing flow is typically organized as location/search controls, category and filter controls, followed by merchant cards. These structural descriptions are only for recognizing that the snapshot is a same-route state; they do not create output fields.

## Semantic alias

Assign alias `1` to the root page region that spatially contains the entire current snapshot. This root anchor is stable across address and listing states and establishes page scope without targeting any personal or incidental text. The extractor should locate this anchor but must still emit only `{}`.

## Ambiguities and privacy

Address labels, recipient names, phone numbers, permission/error notices, category labels, and merchant-card text are all unrequested. Ignore them even if visually clear. A full-screen clickable/image layer or sparse accessibility tree can coexist with rich rendered text; this does not affect the empty output.

## When to rewrite

Rewrite this guide if either schema gains a field, if a non-empty merchant identity is formally requested, or if a more specific semantic anchor becomes necessary to scope requested information. At that point document the new field-to-region relationship, lookalikes, missing-value behavior, and required numeric aliases without hardcoding current labels, node IDs, or coordinates.