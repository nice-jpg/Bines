const test = require('node:test');
const assert = require('node:assert/strict');

const { CollectorClient } = require('../src/collector_client');

test('collector client post wrappers', async () => {
  const calls = [];
  const originalFetch = global.fetch;
  global.fetch = async (url, opts = {}) => {
    calls.push({ url, opts });
    if (String(url).endsWith('/health')) {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    if (String(url).includes('/queue_stats')) {
      return { ok: true, json: async () => ({ ok: true, tasks_by_status: [] }) };
    }
    if (String(url).includes('/quality_report')) {
      return { ok: true, json: async () => ({ ok: true, report: {} }) };
    }
    if (String(url).includes('/manual_review_queue')) {
      return { ok: true, json: async () => ({ ok: true, items: [] }) };
    }
    if (String(url).endsWith('/pull_task')) {
      return { ok: true, json: async () => ({ ok: true, task: { task_id: 't1', platform: 'meituan' } }) };
    }
    return { ok: true, json: async () => ({ ok: true }) };
  };

  try {
    const c = new CollectorClient({ serverUrl: 'http://example.local' });
    const h = await c.health();
    assert.equal(h.ok, true);
    const qs = await c.queueStats();
    assert.equal(qs.ok, true);
    const qr = await c.qualityReport({ lookbackHours: 24 });
    assert.equal(qr.ok, true);
    const mq = await c.manualReviewQueue({ status: 'PENDING', limit: 10 });
    assert.equal(mq.ok, true);

    const t = await c.pullTask({ deviceId: 'd1', platforms: ['meituan'] });
    assert.equal(t.task_id, 't1');

    await c.pushResult({ task_id: 't1', status: 'SUCCESS' });
    await c.heartbeat({ deviceId: 'd1', accountId: 'a1', platform: 'meituan', status: 'IDLE' });

    assert.ok(calls.length >= 4);
  } finally {
    global.fetch = originalFetch;
  }
});
