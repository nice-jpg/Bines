const test = require('node:test');
const assert = require('node:assert/strict');

const {
  parseChineseNumber,
  nameSimilarity,
  bestMatchForPoi,
  fuseStoreFacts,
  buildQualityReport,
} = require('../scripts/store_facts_pipeline');

test('parseChineseNumber handles 万 unit', () => {
  assert.equal(parseChineseNumber('1.2万'), 12000);
  assert.equal(parseChineseNumber('345'), 345);
  assert.equal(parseChineseNumber(''), null);
});

test('bestMatchForPoi respects threshold', () => {
  const poi = { name: '小袁羊肉馆(银河二路)', lat: 33.6496, lng: 116.9641 };
  const records = [
    { store_name: '小袁羊肉馆', lat: 33.64961, lng: 116.96409, source: 'meituan' },
    { store_name: '无关门店', lat: 33.6, lng: 116.9, source: 'meituan' },
  ];
  const hit = bestMatchForPoi(poi, records, 0.75);
  assert.ok(hit);
  const miss = bestMatchForPoi(poi, records, 0.99);
  assert.equal(miss, null);
});

test('fuseStoreFacts combines rating and orders', () => {
  const out = fuseStoreFacts([
    { source: 'meituan', rating: 4.5, review_count: 120, monthly_orders: 300, lifetime_sales: 1200, avg_price: 40, match_confidence: 0.9 },
    { source: 'dianping', rating: 4.2, review_count: 80, monthly_orders: 260, lifetime_sales: 1100, avg_price: 45, match_confidence: 0.8 },
  ]);
  assert.ok(out.rating_fused >= 4.2 && out.rating_fused <= 4.5);
  assert.ok(out.monthly_orders > 0);
  assert.ok(out.lifetime_sales > 0);
  assert.equal(out.matched_source, 'meituan|dianping');
});

test('buildQualityReport includes missing stats', () => {
  const report = buildQualityReport([
    { matched_source: 'meituan', match_confidence: 0.9, rating_fused: 4.2, review_count_fused: 20, monthly_orders: '', lifetime_sales: '' },
    { matched_source: '', match_confidence: '', rating_fused: '', review_count_fused: '', monthly_orders: '', lifetime_sales: '' },
  ]);
  assert.ok(report.match_success_rate >= 0 && report.match_success_rate <= 1);
  assert.ok('missing_field_stats' in report);
});
