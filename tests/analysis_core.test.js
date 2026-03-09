const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
  loadPoi,
  loadDictionary,
  loadRegionContext,
  deduplicatePoi,
  filterRegion,
  assignIndustry,
  computeScores,
} = require('../analysis_core');

const root = path.resolve(__dirname, '..');

test('analysis_core computes full industry scorecard', () => {
  let poi = loadPoi(path.join(root, 'data', 'poi_snapshot.csv'));
  const dict = loadDictionary(path.join(root, 'data', 'industry_dictionary.json'));
  const ctx = loadRegionContext(path.join(root, 'data', 'region_context.json'));
  poi = assignIndustry(filterRegion(deduplicatePoi(poi), 31.2304, 121.4737, 1.5), dict);
  const scores = computeScores(poi, ctx);
  assert.equal(scores.length, 6);
  assert.ok(scores[0].total_score >= scores[scores.length - 1].total_score);
});
