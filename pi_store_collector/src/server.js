#!/usr/bin/env node
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { getConfig } = require('./config');
const { openDb } = require('./db');
const { enqueueFromPoiCsv, pullTask, pushResult, upsertHeartbeat } = require('./task_service');
const { getQueueStats, listManualReviewQueue, buildQualityReport } = require('./report_service');

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(`${JSON.stringify(body)}\n`);
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let buf = '';
    req.on('data', (chunk) => {
      buf += chunk;
      if (buf.length > 5 * 1024 * 1024) {
        reject(new Error('payload too large'));
      }
    });
    req.on('end', () => {
      if (!buf.trim()) return resolve({});
      try {
        resolve(JSON.parse(buf));
      } catch {
        reject(new Error('invalid json'));
      }
    });
    req.on('error', reject);
  });
}

function parseQuery(rawUrl) {
  const urlObj = new URL(rawUrl, 'http://127.0.0.1');
  return {
    path: urlObj.pathname,
    query: Object.fromEntries(urlObj.searchParams.entries()),
  };
}

function startServer() {
  const config = getConfig();
  fs.mkdirSync(path.dirname(path.resolve(config.dbPath)), { recursive: true });
  const db = openDb(config.dbPath);

  const server = http.createServer(async (req, res) => {
    const method = req.method || 'GET';
    const parsed = parseQuery(req.url || '/');
    const url = parsed.path;
    const query = parsed.query;

    try {
      if (method === 'GET' && url === '/health') {
        return json(res, 200, { ok: true, service: 'pi-store-collector', db_path: config.dbPath });
      }

      if (method === 'GET' && url === '/queue_stats') {
        return json(res, 200, { ok: true, ...getQueueStats(db) });
      }

      if (method === 'GET' && url === '/manual_review_queue') {
        const status = query.status || 'PENDING';
        const limit = Number(query.limit || 50);
        const rows = listManualReviewQueue(db, { status, limit });
        return json(res, 200, { ok: true, count: rows.length, items: rows });
      }

      if (method === 'GET' && url === '/quality_report') {
        const lookbackHours = Number(query.lookback_hours || 24);
        const report = buildQualityReport(db, { lookbackHours });
        return json(res, 200, { ok: true, report });
      }

      if (method === 'POST' && url === '/enqueue_from_csv') {
        const body = await readJsonBody(req);
        const poiCsvPath = body.poi_csv_path;
        if (!poiCsvPath) return json(res, 400, { error: 'poi_csv_path is required' });
        const platforms = Array.isArray(body.platforms) && body.platforms.length
          ? body.platforms
          : ['meituan', 'dianping', 'douyin'];
        const result = enqueueFromPoiCsv(db, poiCsvPath, platforms);
        return json(res, 200, { ok: true, ...result });
      }

      if (method === 'POST' && url === '/pull_task') {
        const body = await readJsonBody(req);
        if (!body.device_id) return json(res, 400, { error: 'device_id is required' });
        const task = pullTask(db, {
          deviceId: body.device_id,
          platforms: Array.isArray(body.platforms) ? body.platforms : [],
        });
        return json(res, 200, { ok: true, task: task || null });
      }

      if (method === 'POST' && url === '/push_result') {
        const body = await readJsonBody(req);
        const result = pushResult(db, config, body);
        return json(res, 200, { ok: true, ...result });
      }

      if (method === 'POST' && url === '/heartbeat') {
        const body = await readJsonBody(req);
        if (!body.device_id || !body.platform) {
          return json(res, 400, { error: 'device_id and platform are required' });
        }
        const result = upsertHeartbeat(db, body);
        return json(res, 200, { ok: true, ...result });
      }

      return json(res, 404, { error: 'not found' });
    } catch (err) {
      return json(res, 500, { error: err.message || String(err) });
    }
  });

  server.listen(config.port, () => {
    process.stdout.write(
      `${JSON.stringify({
        ok: true,
        message: 'pi-store-collector started',
        port: config.port,
        db_path: config.dbPath,
      })}\n`,
    );
  });

  return server;
}

if (require.main === module) {
  startServer();
}

module.exports = {
  startServer,
};
