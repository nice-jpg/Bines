const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { withTransaction, nowIso } = require('./db');

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  if (!lines.length) return [];
  const headers = lines[0].split(',').map((x) => x.trim());
  return lines.slice(1).filter(Boolean).map((line) => {
    const cols = line.split(',');
    const row = {};
    headers.forEach((h, i) => {
      row[h] = (cols[i] || '').trim();
    });
    return row;
  });
}

function makeTaskId(platform, storeName, lat, lng, scheduledAt) {
  const raw = `${platform}|${storeName}|${Number(lat).toFixed(5)}|${Number(lng).toFixed(5)}|${scheduledAt}`;
  return crypto.createHash('sha1').update(raw).digest('hex').slice(0, 20);
}

function enqueueFromPoiCsv(db, poiCsvPath, platforms = ['meituan', 'dianping', 'douyin']) {
  const rows = parseCsv(fs.readFileSync(path.resolve(poiCsvPath), 'utf8'));
  const now = nowIso();
  const ins = db.prepare(`
    INSERT OR IGNORE INTO tasks (
      task_id, platform, store_name, lat, lng, review_limit, product_limit,
      status, priority, scheduled_at, payload_json, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, 30, 20, 'PENDING', 0, ?, ?, ?, ?)
  `);

  let count = 0;
  withTransaction(db, () => {
    for (const r of rows) {
      for (const platform of platforms) {
        const scheduled = now;
        const taskId = makeTaskId(platform, r.name, Number(r.lat), Number(r.lng), scheduled.slice(0, 10));
        const payload = JSON.stringify({ category_l2: r.category_l2 || '' });
        ins.run(taskId, platform, r.name, Number(r.lat), Number(r.lng), scheduled, payload, now, now);
        count += 1;
      }
    }
  });

  return { input_store_count: rows.length, inserted_task_count: count };
}

function pullTask(db, { deviceId, platforms = [] }) {
  const now = nowIso();
  const list = (platforms || []).length ? platforms : ['meituan', 'dianping', 'douyin'];
  const placeholders = list.map(() => '?').join(',');

  return withTransaction(db, () => {
    const sel = db.prepare(`
      SELECT * FROM tasks
      WHERE status='PENDING'
        AND platform IN (${placeholders})
      ORDER BY priority DESC, scheduled_at ASC, created_at ASC
      LIMIT 1
    `);
    const task = sel.get(...list);
    if (!task) return null;

    const upd = db.prepare(`
      UPDATE tasks
      SET status='RUNNING', started_at=?, updated_at=?, device_id=?
      WHERE task_id=?
    `);
    upd.run(now, now, deviceId || '', task.task_id);

    return {
      task_id: task.task_id,
      platform: task.platform,
      store_name: task.store_name,
      lat: task.lat,
      lng: task.lng,
      review_limit: task.review_limit,
      product_limit: task.product_limit,
      payload: task.payload_json ? JSON.parse(task.payload_json) : {},
    };
  });
}

function insertStoreFacts(db, task, storeFacts) {
  if (!storeFacts) return;
  const now = nowIso();
  const ins = db.prepare(`
    INSERT INTO store_facts (
      task_id, platform, platform_store_id, store_name, lat, lng,
      rating, review_count, monthly_orders, avg_price, crawl_time, confidence, raw_json, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  ins.run(
    task.task_id,
    task.platform,
    storeFacts.platform_store_id || '',
    storeFacts.store_name || task.store_name,
    Number.isFinite(Number(storeFacts.lat)) ? Number(storeFacts.lat) : null,
    Number.isFinite(Number(storeFacts.lng)) ? Number(storeFacts.lng) : null,
    Number.isFinite(Number(storeFacts.rating)) ? Number(storeFacts.rating) : null,
    Number.isFinite(Number(storeFacts.review_count)) ? Number(storeFacts.review_count) : null,
    Number.isFinite(Number(storeFacts.monthly_orders)) ? Number(storeFacts.monthly_orders) : null,
    Number.isFinite(Number(storeFacts.avg_price)) ? Number(storeFacts.avg_price) : null,
    storeFacts.crawl_time || now,
    Number.isFinite(Number(storeFacts.confidence)) ? Number(storeFacts.confidence) : null,
    JSON.stringify(storeFacts),
    now,
  );
}

function insertProductFacts(db, task, products) {
  if (!Array.isArray(products) || !products.length) return;
  const now = nowIso();
  const ins = db.prepare(`
    INSERT INTO product_facts (
      task_id, platform, store_name, product_id, product_name, price, sales_text, rank_no, crawl_time, raw_json, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  for (let i = 0; i < products.length; i += 1) {
    const p = products[i] || {};
    ins.run(
      task.task_id,
      task.platform,
      task.store_name,
      p.product_id || '',
      p.product_name || `product_${i + 1}`,
      Number.isFinite(Number(p.price)) ? Number(p.price) : null,
      p.sales_text || '',
      Number.isFinite(Number(p.rank_no)) ? Number(p.rank_no) : i + 1,
      p.crawl_time || now,
      JSON.stringify(p),
      now,
    );
  }
}

function insertReviewFacts(db, task, reviews) {
  if (!Array.isArray(reviews) || !reviews.length) return;
  const now = nowIso();
  const ins = db.prepare(`
    INSERT INTO review_facts (
      task_id, platform, store_name, review_id, rating, content, like_count, comment_time, crawl_time, raw_json, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  for (let i = 0; i < reviews.length; i += 1) {
    const r = reviews[i] || {};
    ins.run(
      task.task_id,
      task.platform,
      task.store_name,
      r.review_id || '',
      Number.isFinite(Number(r.rating)) ? Number(r.rating) : null,
      r.content || '',
      Number.isFinite(Number(r.like_count)) ? Number(r.like_count) : null,
      r.comment_time || '',
      r.crawl_time || now,
      JSON.stringify(r),
      now,
    );
  }
}

function pushResult(db, config, payload) {
  const { task_id: taskId, status, fail_reason: failReason, account_id: accountId, device_id: deviceId } = payload;
  if (!taskId) throw new Error('task_id is required');
  const now = nowIso();

  return withTransaction(db, () => {
    const task = db.prepare('SELECT * FROM tasks WHERE task_id=?').get(taskId);
    if (!task) throw new Error(`task not found: ${taskId}`);

    const normalized = String(status || '').toUpperCase();
    if (normalized === 'SUCCESS' || normalized === 'PARTIAL') {
      insertStoreFacts(db, task, payload.store_facts || null);
      insertProductFacts(db, task, payload.product_facts || []);
      insertReviewFacts(db, task, payload.review_facts || []);

      db.prepare(`
        UPDATE tasks SET
          status='SUCCESS',
          account_id=?,
          device_id=?,
          finished_at=?,
          fail_reason=NULL,
          updated_at=?
        WHERE task_id=?
      `).run(accountId || '', deviceId || '', now, now, taskId);

      db.prepare(`
        INSERT INTO crawl_audit (
          task_id, platform, account_id, device_id, status, fail_reason, retry_count, raw_artifact_path, created_at
        ) VALUES (?, ?, ?, ?, 'SUCCESS', '', ?, ?, ?)
      `).run(taskId, task.platform, accountId || '', deviceId || '', task.retry_count, payload.raw_artifact_path || '', now);

      return { task_id: taskId, status: 'SUCCESS' };
    }

    const nextRetry = Number(task.retry_count || 0) + 1;
    const shouldManual = nextRetry > Number(config.retryLimit ?? 2);

    if (shouldManual) {
      db.prepare(`
        UPDATE tasks SET
          status='MANUAL_REVIEW',
          account_id=?,
          device_id=?,
          retry_count=?,
          fail_reason=?,
          finished_at=?,
          updated_at=?
        WHERE task_id=?
      `).run(accountId || '', deviceId || '', nextRetry, failReason || 'UNKNOWN', now, now, taskId);

      db.prepare(`
        INSERT INTO manual_review_queue (task_id, platform, reason, payload_json, status, created_at)
        VALUES (?, ?, ?, ?, 'PENDING', ?)
      `).run(taskId, task.platform, failReason || 'UNKNOWN', JSON.stringify(payload), now);
    } else {
      db.prepare(`
        UPDATE tasks SET
          status='PENDING',
          account_id=?,
          device_id=?,
          retry_count=?,
          fail_reason=?,
          updated_at=?
        WHERE task_id=?
      `).run(accountId || '', deviceId || '', nextRetry, failReason || 'UNKNOWN', now, taskId);
    }

    db.prepare(`
      INSERT INTO crawl_audit (
        task_id, platform, account_id, device_id, status, fail_reason, retry_count, raw_artifact_path, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      taskId,
      task.platform,
      accountId || '',
      deviceId || '',
      shouldManual ? 'MANUAL_REVIEW' : 'FAILED_RETRY',
      failReason || 'UNKNOWN',
      nextRetry,
      payload.raw_artifact_path || '',
      now,
    );

    return { task_id: taskId, status: shouldManual ? 'MANUAL_REVIEW' : 'RETRY_SCHEDULED', retry_count: nextRetry };
  });
}

function upsertHeartbeat(db, payload) {
  const now = nowIso();
  db.prepare(`
    INSERT INTO heartbeats (device_id, account_id, platform, status, extra_json, last_seen)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(device_id, platform)
    DO UPDATE SET
      account_id=excluded.account_id,
      status=excluded.status,
      extra_json=excluded.extra_json,
      last_seen=excluded.last_seen
  `).run(
    payload.device_id || '',
    payload.account_id || '',
    payload.platform || '',
    payload.status || 'UNKNOWN',
    JSON.stringify(payload.extra || {}),
    now,
  );

  return { ok: true, last_seen: now };
}

module.exports = {
  enqueueFromPoiCsv,
  pullTask,
  pushResult,
  upsertHeartbeat,
  parseCsv,
};
