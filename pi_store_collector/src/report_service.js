const { nowIso } = require('./db');

function getQueueStats(db) {
  const byStatus = db.prepare(`
    SELECT status, COUNT(*) AS count
    FROM tasks
    GROUP BY status
    ORDER BY status ASC
  `).all();

  const byPlatform = db.prepare(`
    SELECT platform, status, COUNT(*) AS count
    FROM tasks
    GROUP BY platform, status
    ORDER BY platform ASC, status ASC
  `).all();

  const manualPending = db.prepare(`
    SELECT COUNT(*) AS count
    FROM manual_review_queue
    WHERE status='PENDING'
  `).get();

  const latestHeartbeat = db.prepare(`
    SELECT device_id, platform, status, last_seen
    FROM heartbeats
    ORDER BY last_seen DESC
    LIMIT 20
  `).all();

  return {
    generated_at: nowIso(),
    tasks_by_status: byStatus,
    tasks_by_platform_status: byPlatform,
    manual_review_pending: Number(manualPending?.count || 0),
    latest_heartbeats: latestHeartbeat,
  };
}

function listManualReviewQueue(db, { status = 'PENDING', limit = 50 } = {}) {
  const lim = Number.isFinite(Number(limit)) ? Math.max(1, Math.min(200, Number(limit))) : 50;
  if (!status || status === 'ALL') {
    return db.prepare(`
      SELECT id, task_id, platform, reason, status, created_at
      FROM manual_review_queue
      ORDER BY created_at DESC
      LIMIT ?
    `).all(lim);
  }
  return db.prepare(`
    SELECT id, task_id, platform, reason, status, created_at
    FROM manual_review_queue
    WHERE status=?
    ORDER BY created_at DESC
    LIMIT ?
  `).all(status, lim);
}

function buildQualityReport(db, { lookbackHours = 24 } = {}) {
  const hours = Number.isFinite(Number(lookbackHours)) ? Math.max(1, Number(lookbackHours)) : 24;
  const since = new Date(Date.now() - hours * 3600 * 1000).toISOString();

  const totals = db.prepare(`
    SELECT
      COUNT(*) AS total_tasks,
      SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) AS success_tasks,
      SUM(CASE WHEN status='MANUAL_REVIEW' THEN 1 ELSE 0 END) AS manual_review_tasks
    FROM tasks
    WHERE created_at >= ?
  `).get(since);

  const storeFactCoverage = db.prepare(`
    SELECT
      COUNT(DISTINCT t.task_id) AS success_task_count,
      COUNT(DISTINCT s.task_id) AS store_fact_task_count
    FROM tasks t
    LEFT JOIN store_facts s ON s.task_id=t.task_id
    WHERE t.created_at >= ?
      AND t.status='SUCCESS'
  `).get(since);

  const lowSignalStores = db.prepare(`
    SELECT COUNT(*) AS count
    FROM store_facts
    WHERE created_at >= ?
      AND (review_count IS NULL OR review_count < 5)
  `).get(since);

  const lowConfidenceStores = db.prepare(`
    SELECT COUNT(*) AS count
    FROM store_facts
    WHERE created_at >= ?
      AND confidence IS NOT NULL
      AND confidence < 0.5
  `).get(since);

  const avgMetrics = db.prepare(`
    SELECT
      AVG(COALESCE(rating, 0)) AS avg_rating,
      AVG(COALESCE(review_count, 0)) AS avg_review_count
    FROM store_facts
    WHERE created_at >= ?
  `).get(since);

  const totalTasks = Number(totals?.total_tasks || 0);
  const successTasks = Number(totals?.success_tasks || 0);
  const manualTasks = Number(totals?.manual_review_tasks || 0);
  const successTaskCount = Number(storeFactCoverage?.success_task_count || 0);
  const storeFactTaskCount = Number(storeFactCoverage?.store_fact_task_count || 0);

  const successRate = totalTasks > 0 ? successTasks / totalTasks : 0;
  const storeFactCoverageRate = successTaskCount > 0 ? storeFactTaskCount / successTaskCount : 0;

  return {
    generated_at: nowIso(),
    lookback_hours: hours,
    totals: {
      total_tasks: totalTasks,
      success_tasks: successTasks,
      manual_review_tasks: manualTasks,
      success_rate: Number(successRate.toFixed(4)),
    },
    data_quality: {
      store_fact_coverage_rate: Number(storeFactCoverageRate.toFixed(4)),
      low_signal_store_count: Number(lowSignalStores?.count || 0),
      low_confidence_store_count: Number(lowConfidenceStores?.count || 0),
      avg_rating: Number((avgMetrics?.avg_rating || 0).toFixed(3)),
      avg_review_count: Number((avgMetrics?.avg_review_count || 0).toFixed(3)),
    },
  };
}

module.exports = {
  getQueueStats,
  listManualReviewQueue,
  buildQualityReport,
};
