#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const { getConfig } = require('../src/config');
const { openDb } = require('../src/db');
const { buildQualityReport } = require('../src/report_service');

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    const val = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : 'true';
    args[key] = val;
  }
  return args;
}

function escapeCsv(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  if (!/[",\n]/.test(s)) return s;
  return `"${s.replace(/"/g, '""')}"`;
}

function writeCsv(filePath, rows, headers) {
  const resolvedHeaders = Array.isArray(headers) && headers.length
    ? headers
    : (rows.length ? Object.keys(rows[0]) : []);
  const lines = [resolvedHeaders.join(',')];
  for (const row of rows) {
    lines.push(resolvedHeaders.map((h) => escapeCsv(row[h])).join(','));
  }
  fs.writeFileSync(filePath, `${lines.join('\n')}\n`, 'utf8');
}

function main() {
  const args = parseArgs(process.argv);
  const config = getConfig();
  const outDir = path.resolve(args['out-dir'] || path.join(config.dataRoot, 'exports'));
  const lookbackHours = Number(args['lookback-hours'] || 24);
  fs.mkdirSync(outDir, { recursive: true });

  const db = openDb(config.dbPath);
  const stores = db.prepare(`
    SELECT task_id, platform, platform_store_id, store_name, lat, lng, rating, review_count,
           monthly_orders, avg_price, crawl_time, confidence, created_at
    FROM store_facts
    ORDER BY id ASC
  `).all();
  const products = db.prepare(`
    SELECT task_id, platform, store_name, product_id, product_name, price, sales_text, rank_no, crawl_time, created_at
    FROM product_facts
    ORDER BY id ASC
  `).all();
  const reviews = db.prepare(`
    SELECT task_id, platform, store_name, review_id, rating, content, like_count, comment_time, crawl_time, created_at
    FROM review_facts
    ORDER BY id ASC
  `).all();
  const report = buildQualityReport(db, { lookbackHours });

  const storesPath = path.join(outDir, 'store_facts.csv');
  const productsPath = path.join(outDir, 'product_facts.csv');
  const reviewsPath = path.join(outDir, 'review_facts.csv');
  const qualityPath = path.join(outDir, 'quality_report.json');

  writeCsv(storesPath, stores, [
    'task_id', 'platform', 'platform_store_id', 'store_name', 'lat', 'lng', 'rating', 'review_count',
    'monthly_orders', 'avg_price', 'crawl_time', 'confidence', 'created_at',
  ]);
  writeCsv(productsPath, products, [
    'task_id', 'platform', 'store_name', 'product_id', 'product_name', 'price', 'sales_text',
    'rank_no', 'crawl_time', 'created_at',
  ]);
  writeCsv(reviewsPath, reviews, [
    'task_id', 'platform', 'store_name', 'review_id', 'rating', 'content', 'like_count',
    'comment_time', 'crawl_time', 'created_at',
  ]);
  fs.writeFileSync(qualityPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');

  process.stdout.write(`${JSON.stringify({
    ok: true,
    out_dir: outDir,
    files: {
      store_facts_csv: storesPath,
      product_facts_csv: productsPath,
      review_facts_csv: reviewsPath,
      quality_report_json: qualityPath,
    },
    counts: {
      stores: stores.length,
      products: products.length,
      reviews: reviews.length,
    },
  })}\n`);
}

if (require.main === module) {
  try {
    main();
  } catch (err) {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  }
}
