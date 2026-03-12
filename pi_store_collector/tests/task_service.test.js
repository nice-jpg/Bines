const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { openDb } = require('../src/db');
const { enqueueFromPoiCsv, pullTask, pushResult, upsertHeartbeat } = require('../src/task_service');

function makeTmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'pi-collector-'));
}

test('enqueue -> pull -> success push result', () => {
  const dir = makeTmp();
  const db = openDb(path.join(dir, 't.db'));
  const csv = path.join(dir, 'poi.csv');
  fs.writeFileSync(csv, 'name,category_l2,lat,lng\n小袁羊肉馆,中式快餐,33.64,116.96\n', 'utf8');

  const enq = enqueueFromPoiCsv(db, csv, ['meituan']);
  assert.equal(enq.input_store_count, 1);

  const task = pullTask(db, { deviceId: 'android-01', platforms: ['meituan'] });
  assert.ok(task);
  assert.equal(task.platform, 'meituan');

  const out = pushResult(db, { retryLimit: 2 }, {
    task_id: task.task_id,
    status: 'SUCCESS',
    account_id: 'acc-01',
    device_id: 'android-01',
    store_facts: { store_name: '小袁羊肉馆', rating: 4.5, review_count: 100, monthly_orders: 200, avg_price: 45 },
    product_facts: [{ product_name: '羊肉汤', price: 26, rank_no: 1 }],
    review_facts: [{ rating: 5, content: '不错', like_count: 3 }],
  });

  assert.equal(out.status, 'SUCCESS');
  const row = db.prepare('SELECT status FROM tasks WHERE task_id=?').get(task.task_id);
  assert.equal(row.status, 'SUCCESS');
});

test('failed push should move to manual review after retry limit', () => {
  const dir = makeTmp();
  const db = openDb(path.join(dir, 't.db'));
  const csv = path.join(dir, 'poi.csv');
  fs.writeFileSync(csv, 'name,category_l2,lat,lng\n爱萌宠物店,生活服务综合,33.64,116.96\n', 'utf8');

  enqueueFromPoiCsv(db, csv, ['dianping']);
  const task = pullTask(db, { deviceId: 'android-02', platforms: ['dianping'] });
  assert.ok(task);

  const r1 = pushResult(db, { retryLimit: 0 }, {
    task_id: task.task_id,
    status: 'FAILED',
    fail_reason: 'CAPTCHA',
    account_id: 'acc-02',
    device_id: 'android-02',
  });

  assert.equal(r1.status, 'MANUAL_REVIEW');
  const t = db.prepare('SELECT status FROM tasks WHERE task_id=?').get(task.task_id);
  assert.equal(t.status, 'MANUAL_REVIEW');
  const mq = db.prepare('SELECT COUNT(*) AS c FROM manual_review_queue WHERE task_id=?').get(task.task_id);
  assert.equal(mq.c, 1);
});

test('heartbeat upsert works', () => {
  const dir = makeTmp();
  const db = openDb(path.join(dir, 't.db'));
  const h = upsertHeartbeat(db, { device_id: 'android-03', platform: 'douyin', status: 'IDLE', extra: { load: 0.2 } });
  assert.equal(h.ok, true);
  const row = db.prepare('SELECT status FROM heartbeats WHERE device_id=? AND platform=?').get('android-03', 'douyin');
  assert.equal(row.status, 'IDLE');
});
