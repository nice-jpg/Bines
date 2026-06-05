const test = require('node:test');
const assert = require('node:assert/strict');

const {
  nameSimilarity,
  deriveRawProxyMetrics,
  computeDemandProxy,
  buildQualityReport,
} = require('../scripts/enrich_store_facts');

test('name similarity should handle store suffix', () => {
  assert.ok(nameSimilarity('小袁羊肉馆(银河二路)', '小袁羊肉馆') > 0.75);
});

test('derive raw proxy metrics handles zero reviews', () => {
  const raw = deriveRawProxyMetrics([], { fused_review_count: 0 });
  assert.equal(raw.review_count_30d, 0);
  assert.equal(raw.review_count_7d, 0);
  assert.equal(raw.review_growth_7d_vs_30d_raw, 0);
});

test('demand proxy score in [0, 100] and level assigned', () => {
  const rows = [
    {
      name: 'A', review_count_7d: 3, review_count_30d: 20, review_growth_7d_vs_30d_raw: (3 * 4) / 21,
      review_activity_days_30d: 10, rating_stability_30d: 0.2,
    },
    {
      name: 'B', review_count_7d: 0, review_count_30d: 1, review_growth_7d_vs_30d_raw: 0,
      review_activity_days_30d: 1, rating_stability_30d: 0.8,
    },
  ];
  const out = computeDemandProxy(rows);
  assert.equal(out.length, 2);
  for (const r of out) {
    assert.ok(r.demand_proxy_score >= 0 && r.demand_proxy_score <= 100);
    assert.ok(['A', 'B', 'C'].includes(r.demand_proxy_level));
  }
  assert.equal(out[1].low_signal_flag, 1);
});

test('quality report has required metrics', () => {
  const report = buildQualityReport([
    { demand_proxy_score: 65, low_signal_flag: 0 },
    { demand_proxy_score: 12, low_signal_flag: 1 },
  ]);
  assert.equal(report.order_truth_coverage_rate, 0);
  assert.equal(report.low_signal_store_count, 1);
  assert.ok(report.proxy_coverage_rate > 0);
});
