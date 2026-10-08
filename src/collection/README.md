# Continuous Collection

`collect_continuously(x, y, change_threshold)` is a conductor-only tool. Existing
manual tools remain available. Start it after successful visit-specific collector
training, restoration, and identification of the product swipe coordinates.

## Training Contract

Collector upward swipes expose an `operation_id`. After saving independent visual
baselines for the resulting states and passing full regression, collector calls
`confirm_normal_swipes(operation_ids)`. At least one short and one long upward
swipe from this visit are required. Random jumps and historical cases cannot
supply swipe samples. The threshold is `min(1, max(ratios) + 0.05)`.

`prepare_collector` returns `normal_swipe_profile` with `change_threshold`,
`sample_count`, `actions`, and a warning if the threshold saturates at 1. Pass the
exact threshold back to the tool. A missing profile disables only the automatic
loop. Changed scripts or contracts require fresh validation.

## Execution Boundary

`ContinuousCollection` owns the loop; `EventHub.collection_swipe` owns gestures
and Reco synchronization; `CollectionService` owns extraction, admission, workbook
deduplication, audit sampling and progress. The ordinary collection middleware
and `is_done` use this same service. No nested LangChain tool execution occurs.

Each normal step persists immediately. Incomplete rows select a short next swipe
and break empty-swipe evidence; otherwise the next swipe is long. Three complete,
successfully persisted zero-new steps confirm completion. A root change above
the trained threshold stops before extraction. Device, script, contract, alias
and persistence errors return control without losing prior successful writes.

The loop has no step limit. Incoming user input and runtime shutdown yield at
finite-action boundaries without consuming messages. The shared device/view
lock prevents other operations from interleaving. Async tool invocation uses
LangChain's worker execution of the same synchronous function.

The result contains `status` (`completed`, `attention_required`, or `yielded`),
`reason`, `swipes`, `appended_items`, `change_ratio`, and normally a compact
`page_hint`. Excessive-change results omit page details. One final Command commits
the accumulated state; internal snapshots and item data are not added to model
history. Process termination can lose uncheckpointed loop state, not successful
workbook writes.
