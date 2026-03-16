const test = require('node:test');
const assert = require('node:assert/strict');

const { buildAdapters } = require('../src/adapters');

test('buildAdapters uses meituan adb by default', () => {
  const adapters = buildAdapters();
  assert.equal(adapters.meituan.name, 'meituan-adb-adapter');
  assert.equal(adapters.dianping.name, 'dianping-stub-adapter');
  assert.equal(adapters.douyin.name, 'douyin-stub-adapter');
});
