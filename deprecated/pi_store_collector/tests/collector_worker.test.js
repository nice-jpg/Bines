const test = require('node:test');
const assert = require('node:assert/strict');

const { runSingleIteration } = require('../src/collector_worker');

test('worker single iteration success flow', async () => {
  const events = [];
  const client = {
    async pullTask() {
      events.push('pull');
      return { task_id: 't100', platform: 'meituan', store_name: '小袁羊肉馆', lat: 33.64, lng: 116.96, review_limit: 30, product_limit: 20 };
    },
    async heartbeat(payload) {
      events.push(`heartbeat:${payload.status}`);
    },
    async pushResult(payload) {
      events.push(`push:${payload.status}`);
      assert.equal(payload.task_id, 't100');
    },
  };
  const adapters = {
    meituan: {
      async collect() {
        return {
          store_facts: { store_name: '小袁羊肉馆', rating: 4.5 },
          product_facts: [{ product_name: '羊肉汤', price: 26 }],
          review_facts: [{ rating: 5, content: '好吃' }],
        };
      },
    },
  };

  const out = await runSingleIteration({
    client,
    adapters,
    deviceId: 'android-01',
    accountId: 'acc-01',
    platforms: ['meituan'],
  });

  assert.equal(out.status, 'SUCCESS');
  assert.ok(events.includes('pull'));
  assert.ok(events.includes('heartbeat:WORKING'));
  assert.ok(events.includes('push:SUCCESS'));
});
