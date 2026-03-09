const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

const { MCPServer } = require('../mcp_server');

const root = path.resolve(__dirname, '..');

test('mcp server run_full_analysis core path', () => {
  const server = new MCPServer();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'market-js-'));
  const res = server.callRunFullAnalysis(
    {
      poi_path: path.join(root, 'data', 'poi_snapshot.csv'),
      context_path: path.join(root, 'data', 'region_context.json'),
      dictionary_path: path.join(root, 'data', 'industry_dictionary.json'),
      center_lat: 31.2304,
      center_lng: 121.4737,
      radius_km: 1.5,
      output_dir: tmp,
    },
    false,
  );
  assert.equal(res.top3.length, 3);
  assert.ok(fs.existsSync(path.join(tmp, 'industry_scorecard.csv')));
});
