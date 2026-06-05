#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { loadEnvFile } = require('./map_service_env');

const DEFAULT_MATCH_THRESHOLD = 0.75;

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

function required(args, key) {
  if (!args[key]) throw new Error(`Missing required arg --${key}`);
  return args[key];
}

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

function toCsv(headers, rows) {
  const out = [headers.join(',')];
  for (const row of rows) {
    out.push(headers.map((h) => String(row[h] ?? '')).join(','));
  }
  return `${out.join('\n')}\n`;
}

function normalizeName(name) {
  return String(name || '')
    .toLowerCase()
    .replace(/[\s·•()（）\-—_]/g, '')
    .replace(/(旗舰店|分店|店|门店|总店|店铺)$/g, '');
}

function nameSimilarity(a, b) {
  const x = normalizeName(a);
  const y = normalizeName(b);
  if (!x || !y) return 0;
  if (x === y) return 1;
  if (x.includes(y) || y.includes(x)) return 0.86;
  const overlap = [...new Set(x)].filter((ch) => y.includes(ch)).length;
  return overlap / Math.max(x.length, y.length);
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const r = 6371.0;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dphi = ((lat2 - lat1) * Math.PI) / 180;
  const dlambda = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dlambda / 2) ** 2;
  return 2 * r * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function parseChineseNumber(value) {
  const txt = String(value || '').trim();
  if (!txt) return null;
  const m = txt.match(/([0-9]+(?:\.[0-9]+)?)(万?)/);
  if (!m) return null;
  const n = Number(m[1]);
  if (!Number.isFinite(n)) return null;
  return m[2] === '万' ? Math.round(n * 10000) : Math.round(n);
}

function pickNumber(row, keys) {
  for (const k of keys) {
    if (!(k in row)) continue;
    const raw = row[k];
    const direct = Number(raw);
    if (Number.isFinite(direct) && direct >= 0) return direct;
    const parsed = parseChineseNumber(raw);
    if (parsed != null) return parsed;
  }
  return null;
}

function storeIdFromPoi(poi) {
  const base = `${normalizeName(poi.name)}|${Number(poi.lat).toFixed(5)}|${Number(poi.lng).toFixed(5)}`;
  return crypto.createHash('sha1').update(base).digest('hex').slice(0, 16);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function fetchText(url, timeoutMs = 25000, headers = {}) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      signal: controller.signal,
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; MarketFactsBot/1.0)',
        ...headers,
      },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.text();
  } finally {
    clearTimeout(t);
  }
}

function extractMetricByRegex(text, patterns) {
  for (const p of patterns) {
    const m = text.match(p);
    if (m && m[1]) {
      const n = parseChineseNumber(m[1]);
      if (n != null) return n;
      const v = Number(m[1]);
      if (Number.isFinite(v)) return v;
    }
  }
  return null;
}

function extractRating(text) {
  const candidates = [
    /ratingValue[^0-9]*([0-5](?:\.[0-9])?)/i,
    /评分[^0-9]*([0-5](?:\.[0-9])?)/,
    /star[^0-9]*([0-5](?:\.[0-9])?)/i,
  ];
  return extractMetricByRegex(text, candidates);
}

function extractReviewCount(text) {
  const candidates = [
    /(?:评价|评论)\s*([0-9]+(?:\.[0-9]+)?万?)/,
    /comment[^0-9]*([0-9]+(?:\.[0-9]+)?万?)/i,
  ];
  return extractMetricByRegex(text, candidates);
}

function extractMonthlyOrders(text) {
  const candidates = [
    /(?:月售|月销)\s*([0-9]+(?:\.[0-9]+)?万?)/,
    /monthly[^0-9]*([0-9]+(?:\.[0-9]+)?万?)/i,
  ];
  return extractMetricByRegex(text, candidates);
}

function extractLifetimeSales(text) {
  const candidates = [
    /(?:累计|总销量)\s*([0-9]+(?:\.[0-9]+)?万?)/,
    /total[^0-9]*sales[^0-9]*([0-9]+(?:\.[0-9]+)?万?)/i,
  ];
  return extractMetricByRegex(text, candidates);
}

function extractAvgPrice(text) {
  const candidates = [
    /(?:人均|客单价|平均消费)\s*[¥￥]?\s*([0-9]+(?:\.[0-9]+)?)/,
    /avg[^0-9]*(?:price|cost)[^0-9]*([0-9]+(?:\.[0-9]+)?)/i,
  ];
  return extractMetricByRegex(text, candidates);
}

function normalizeSourceRecord(row, source) {
  return {
    source,
    store_name: row.store_name || row.name || '',
    lat: pickNumber(row, ['lat', 'latitude']),
    lng: pickNumber(row, ['lng', 'lon', 'longitude']),
    rating: pickNumber(row, ['rating']),
    review_count: pickNumber(row, ['review_count', 'comment_count']),
    monthly_orders: pickNumber(row, ['monthly_orders', 'order_count', 'month_sales']),
    lifetime_sales: pickNumber(row, ['lifetime_sales', 'total_sales']),
    avg_price: pickNumber(row, ['avg_price', 'price', 'cost']),
    raw_url: row.url || row.raw_url || '',
    snapshot_time: row.snapshot_time || new Date().toISOString(),
    raw_id: row.raw_id || row.id || '',
  };
}

function loadCsvSource(csvPath, source) {
  if (!csvPath) return [];
  const rows = parseCsv(fs.readFileSync(path.resolve(csvPath), 'utf8'));
  return rows.map((r) => normalizeSourceRecord(r, source));
}

async function fetchWebSource(urlCsvPath, source, delayMs) {
  if (!urlCsvPath) return [];
  const rows = parseCsv(fs.readFileSync(path.resolve(urlCsvPath), 'utf8'));
  const out = [];
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i];
    const url = row.url || row.raw_url;
    if (!url) continue;
    try {
      const html = await fetchText(url);
      out.push({
        source,
        store_name: row.store_name || row.name || '',
        lat: pickNumber(row, ['lat', 'latitude']),
        lng: pickNumber(row, ['lng', 'lon', 'longitude']),
        rating: extractRating(html),
        review_count: extractReviewCount(html),
        monthly_orders: extractMonthlyOrders(html),
        lifetime_sales: extractLifetimeSales(html),
        avg_price: extractAvgPrice(html),
        raw_url: url,
        snapshot_time: new Date().toISOString(),
        raw_id: row.raw_id || row.id || '',
      });
    } catch {
      out.push({
        source,
        store_name: row.store_name || row.name || '',
        lat: pickNumber(row, ['lat', 'latitude']),
        lng: pickNumber(row, ['lng', 'lon', 'longitude']),
        rating: null,
        review_count: null,
        monthly_orders: null,
        lifetime_sales: null,
        avg_price: null,
        raw_url: url,
        snapshot_time: new Date().toISOString(),
        raw_id: row.raw_id || row.id || '',
      });
    }
    if (i < rows.length - 1) await sleep(delayMs);
  }
  return out;
}

function bestMatchForPoi(poi, records, threshold) {
  let best = null;
  let bestScore = -1;
  for (const r of records) {
    const nameScore = nameSimilarity(poi.name, r.store_name);
    const hasDistance =
      Number.isFinite(poi.lat) && Number.isFinite(poi.lng) && Number.isFinite(r.lat) && Number.isFinite(r.lng);
    let score = hasDistance ? nameScore * 0.7 : nameScore;
    if (hasDistance) {
      const d = haversineKm(poi.lat, poi.lng, r.lat, r.lng);
      score += Math.max(0, 0.2 - d * 0.08);
    }
    if (score > bestScore) {
      best = r;
      bestScore = score;
    }
  }
  if (!best || bestScore < threshold) return null;
  return { ...best, match_confidence: Number(bestScore.toFixed(3)) };
}

function weightedMean(values, weights) {
  let sumW = 0;
  let sumV = 0;
  for (const { source, value } of values) {
    if (value == null || Number.isNaN(value)) continue;
    const w = weights[source] || 1;
    sumW += w;
    sumV += value * w;
  }
  return sumW > 0 ? Number((sumV / sumW).toFixed(2)) : null;
}

function fuseStoreFacts(perSource) {
  const items = perSource.filter(Boolean);
  return {
    rating_fused: weightedMean(items.map((x) => ({ source: x.source, value: x.rating })), { meituan: 1.0, dianping: 1.0, amap: 0.6 }),
    review_count_fused: weightedMean(items.map((x) => ({ source: x.source, value: x.review_count })), { meituan: 1.0, dianping: 1.0, amap: 0.5 }),
    monthly_orders: weightedMean(items.map((x) => ({ source: x.source, value: x.monthly_orders })), { meituan: 1.0, dianping: 0.8, amap: 0.0 }),
    lifetime_sales: weightedMean(items.map((x) => ({ source: x.source, value: x.lifetime_sales })), { meituan: 1.0, dianping: 0.8, amap: 0.0 }),
    avg_price_fused: weightedMean(items.map((x) => ({ source: x.source, value: x.avg_price })), { meituan: 1.0, dianping: 1.0, amap: 0.7 }),
    matched_source: items.map((x) => x.source).join('|'),
    match_confidence: items.length ? Number((items.reduce((s, x) => s + (x.match_confidence || 0), 0) / items.length).toFixed(3)) : null,
  };
}

function buildQualityReport(rows) {
  const total = rows.length || 1;
  const matchOk = rows.filter((r) => (r.matched_source || '').length > 0).length;
  const lowConfidence = rows.filter((r) => Number.isFinite(r.match_confidence) && r.match_confidence < 0.8).length;

  const missing = {
    rating_fused: rows.filter((r) => !Number.isFinite(r.rating_fused)).length,
    review_count_fused: rows.filter((r) => !Number.isFinite(r.review_count_fused)).length,
    monthly_orders: rows.filter((r) => !Number.isFinite(r.monthly_orders)).length,
    lifetime_sales: rows.filter((r) => !Number.isFinite(r.lifetime_sales)).length,
    avg_price_fused: rows.filter((r) => !Number.isFinite(r.avg_price_fused)).length,
  };

  return {
    total_store_count: rows.length,
    match_success_rate: Number((matchOk / total).toFixed(4)),
    low_confidence_count: lowConfidence,
    missing_field_stats: missing,
    generated_at: new Date().toISOString(),
  };
}

async function main() {
  const args = parseArgs(process.argv);
  const poiPath = path.resolve(required(args, 'poi'));
  const outputPath = path.resolve(required(args, 'output'));
  const threshold = Number(args['match-threshold'] || DEFAULT_MATCH_THRESHOLD);
  const delayMs = Number(args['delay-ms'] || 450);

  loadEnvFile(args['env-file'] || '.env');

  const poiRows = parseCsv(fs.readFileSync(poiPath, 'utf8')).map((r) => ({
    name: r.name,
    category_l2: r.category_l2,
    lat: Number(r.lat),
    lng: Number(r.lng),
  }));

  const meituanCsvRecords = loadCsvSource(args['meituan-csv'], 'meituan');
  const dianpingCsvRecords = loadCsvSource(args['dianping-csv'], 'dianping');
  const meituanWebRecords = await fetchWebSource(args['meituan-url-csv'], 'meituan', delayMs);
  const dianpingWebRecords = await fetchWebSource(args['dianping-url-csv'], 'dianping', delayMs);

  const sourceRecords = {
    meituan: [...meituanCsvRecords, ...meituanWebRecords],
    dianping: [...dianpingCsvRecords, ...dianpingWebRecords],
  };

  const rows = [];
  const snapshotDate = new Date().toISOString().slice(0, 10);

  for (const poi of poiRows) {
    const mMatch = bestMatchForPoi(poi, sourceRecords.meituan, threshold);
    const dMatch = bestMatchForPoi(poi, sourceRecords.dianping, threshold);
    const fused = fuseStoreFacts([mMatch, dMatch]);

    rows.push({
      store_id: storeIdFromPoi(poi),
      snapshot_date: snapshotDate,
      store_name: poi.name,
      category_l2: poi.category_l2,
      lat: poi.lat,
      lng: poi.lng,
      rating_meituan: mMatch?.rating ?? '',
      rating_dianping: dMatch?.rating ?? '',
      rating_fused: fused.rating_fused ?? '',
      review_count_meituan: mMatch?.review_count ?? '',
      review_count_dianping: dMatch?.review_count ?? '',
      review_count_fused: fused.review_count_fused ?? '',
      monthly_orders: fused.monthly_orders ?? '',
      lifetime_sales: fused.lifetime_sales ?? '',
      avg_price_meituan: mMatch?.avg_price ?? '',
      avg_price_dianping: dMatch?.avg_price ?? '',
      avg_price_fused: fused.avg_price_fused ?? '',
      matched_source: fused.matched_source,
      match_confidence: fused.match_confidence ?? '',
      missing_fields: [
        !Number.isFinite(fused.rating_fused) ? 'rating_fused' : null,
        !Number.isFinite(fused.review_count_fused) ? 'review_count_fused' : null,
        !Number.isFinite(fused.monthly_orders) ? 'monthly_orders' : null,
        !Number.isFinite(fused.lifetime_sales) ? 'lifetime_sales' : null,
      ].filter(Boolean).join('|'),
      missing_reason: (!Number.isFinite(fused.monthly_orders) || !Number.isFinite(fused.lifetime_sales))
        ? 'NO_AUTHORIZED_ORDER_SOURCE'
        : '',
      raw_trace_id: [mMatch?.raw_id, dMatch?.raw_id].filter(Boolean).join('|'),
      raw_url: [mMatch?.raw_url, dMatch?.raw_url].filter(Boolean).join('|'),
    });
  }

  const headers = [
    'store_id', 'snapshot_date', 'store_name', 'category_l2', 'lat', 'lng',
    'rating_meituan', 'rating_dianping', 'rating_fused',
    'review_count_meituan', 'review_count_dianping', 'review_count_fused',
    'monthly_orders', 'lifetime_sales',
    'avg_price_meituan', 'avg_price_dianping', 'avg_price_fused',
    'matched_source', 'match_confidence',
    'missing_fields', 'missing_reason', 'raw_trace_id', 'raw_url',
  ];

  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, toCsv(headers, rows), 'utf8');

  const qualityPath = outputPath.endsWith('.csv')
    ? `${outputPath.slice(0, -4)}_quality_report.json`
    : `${outputPath}_quality_report.json`;
  const report = buildQualityReport(rows);
  fs.writeFileSync(qualityPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');

  process.stdout.write(`${JSON.stringify({
    poi_count: poiRows.length,
    output: outputPath,
    quality_report: qualityPath,
    meituan_record_count: sourceRecords.meituan.length,
    dianping_record_count: sourceRecords.dianping.length,
  })}\n`);
}

if (require.main === module) {
  main().catch((err) => {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  });
}

module.exports = {
  parseChineseNumber,
  nameSimilarity,
  bestMatchForPoi,
  fuseStoreFacts,
  buildQualityReport,
};
