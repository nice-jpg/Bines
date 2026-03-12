const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { openDb } = require('../src/db');
const { enqueueFromPoiCsv, pullTask, pushResult, upsertHeartbeat } = require('../src/task_service');
const { getQueueStats, listManualReviewQueue, buildQualityReport } = require('../src/report_service');

function makeTmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'pi-report-'));
}

test('queue stats and quality report are generated', () => {
  const dir = makeTmp();
  const db = openDb(path.join(dir, 't.db'));
  const csv = path.join(dir, 'poi.csv');
  fs.writeFileSync(csv, 'name,category_l2,lat,lng\n小袁羊肉馆,中式快餐,33.64,116.96\n', 'utf8');

  enqueueFromPoiCsv(db, csv, ['meituan']);
  const task = pullTask(db, { deviceId: 'android-01', platforms: ['meituan'] });
  assert.ok(task);

  pushResult(db, { retryLimit: 2 }, {
    task_id: task.task_id,
    status: 'SUCCESS',
    account_id: 'acc-01',
    device_id: 'android-01',
    store_facts: { store_name: '小袁羊肉馆', rating: 4.6, review_count: 9, confidence: 0.7 },
    product_facts: [{ product_name: '羊肉汤', price: 26 }],
    review_facts: [{ rating: 5, content: '不错' }],
  });
  upsertHeartbeat(db, { device_id: 'android-01', account_id: 'acc-01', platform: 'meituan', status: 'IDLE' });

  const qs = getQueueStats(db);
  assert.equal(qs.manual_review_pending, 0);
  assert.ok(Array.isArray(qs.tasks_by_status));
  assert.ok(qs.tasks_by_status.length > 0);

  const report = buildQualityReport(db, { lookbackHours: 48 });
  assert.equal(report.totals.total_tasks, 1);
  assert.equal(report.totals.success_tasks, 1);
  assert.ok(report.data_quality.store_fact_coverage_rate >= 0);
});

test('manual review list supports status filter', () => {
  const dir = makeTmp();
  const db = openDb(path.join(dir, 't.db'));
  const csv = path.join(dir, 'poi.csv');
  fs.writeFileSync(csv, 'name,category_l2,lat,lng\n爱萌宠物店,宠物服务,33.64,116.96\n', 'utf8');

  enqueueFromPoiCsv(db, csv, ['dianping']);
  const task = pullTask(db, { deviceId: 'android-02', platforms: ['dianping'] });
  assert.ok(task);

  pushResult(db, { retryLimit: 0 }, {
    task_id: task.task_id,
    status: 'FAILED',
    fail_reason: 'CAPTCHA',
    account_id: 'acc-02',
    device_id: 'android-02',
  });

  const rows = listManualReviewQueue(db, { status: 'PENDING', limit: 10 });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].reason, 'CAPTCHA');
});
