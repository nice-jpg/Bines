#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const { resolveAmapKey, loadEnvFile } = require('./map_service_env');

const MATCH_THRESHOLD_AMAP = 0.75;
const MATCH_THRESHOLD_CSV = 0.75;
const ORDER_MISSING_REASON = 'NO_AUTHORIZED_ORDER_SOURCE';

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
  for (const r of rows) out.push(headers.map((h) => String(r[h] ?? '')).join(','));
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

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function clamp(v, low, high) {
  return Math.max(low, Math.min(high, v));
}

async function httpGetJson(url, timeoutMs = 25000) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(t);
  }
}

function pickNumber(row, keys) {
  for (const k of keys) {
    if (!(k in row)) continue;
    const v = Number(row[k]);
    if (Number.isFinite(v) && v >= 0) return v;
  }
  return null;
}

async function fetchAmapFact({ key, name, city, lat, lng }) {
  const params = new URLSearchParams({
    key,
    keywords: name,
    city: city || '',
    citylimit: city ? 'true' : 'false',
    offset: '10',
    page: '1',
    extensions: 'all',
  });
  const payload = await httpGetJson(`https://restapi.amap.com/v3/place/text?${params.toString()}`);
  if (payload.status !== '1') return null;
  const pois = payload.pois || [];
  if (!pois.length) return null;

  let best = null;
  let bestScore = -1;
  for (const p of pois) {
    const sim = nameSimilarity(name, p.name || '');
    let score = sim * 0.8;
    if (lat != null && lng != null && String(p.location || '').includes(',')) {
      const [plng, plat] = String(p.location).split(',').map(Number);
      if (Number.isFinite(plat) && Number.isFinite(plng)) {
        const d = haversineKm(lat, lng, plat, plng);
        score += Math.max(0, 0.2 - d * 0.05);
      }
    }
    if (score > bestScore) {
      best = p;
      bestScore = score;
    }
  }
  if (!best || bestScore < MATCH_THRESHOLD_AMAP) return null;

  const biz = best.biz_ext || {};
  return {
    source: 'amap',
    matched_name: best.name || '',
    rating: Number(biz.rating || 0) || null,
    review_count: Number(best.importance || 0) || null,
    avg_price: Number(biz.cost || 0) || null,
    review_count_7d: null,
    review_count_30d: null,
    review_activity_days_30d: null,
    rating_stability_30d: null,
    confidence: Number(bestScore.toFixed(3)),
  };
}

function matchFromCsv(poi, rows, sourceName) {
  if (!rows || !rows.length) return null;
  let best = null;
  let bestScore = -1;
  for (const r of rows) {
    const sim = nameSimilarity(poi.name, r.name || r.store_name || '');
    if (sim > bestScore) {
      best = r;
      bestScore = sim;
    }
  }
  if (!best || bestScore < MATCH_THRESHOLD_CSV) return null;

  return {
    source: sourceName,
    matched_name: best.name || best.store_name || '',
    rating: pickNumber(best, ['rating']),
    review_count: pickNumber(best, ['review_count', 'comment_count']),
    avg_price: pickNumber(best, ['avg_price', 'price']),
    review_count_7d: pickNumber(best, ['review_count_7d', 'comments_7d']),
    review_count_30d: pickNumber(best, ['review_count_30d', 'comments_30d']),
    review_activity_days_30d: pickNumber(best, ['review_activity_days_30d', 'active_days_30d']),
    rating_stability_30d: pickNumber(best, ['rating_stability_30d', 'rating_std_30d']),
    confidence: Number(bestScore.toFixed(3)),
  };
}

function weightedMean(clean, field, weights) {
  let weighted = 0;
  let sumW = 0;
  for (const f of clean) {
    const v = f[field];
    if (v == null || Number.isNaN(v)) continue;
    const w = weights[f.source] || 1;
    weighted += v * w;
    sumW += w;
  }
  return sumW > 0 ? Number((weighted / sumW).toFixed(2)) : null;
}

function fuseFacts(facts) {
  const clean = facts.filter(Boolean);
  return {
    fused_rating: weightedMean(clean, 'rating', { dianping: 1.0, meituan: 1.0, amap: 0.6 }),
    fused_review_count: weightedMean(clean, 'review_count', { dianping: 1.0, meituan: 1.0, amap: 0.5 }),
    // 明确不输出真实订单数。
    fused_order_count: null,
    order_missing_reason: ORDER_MISSING_REASON,
    data_sources: clean.map((x) => x.source).join('|'),
    match_confidence: clean.length
      ? Number((clean.reduce((s, x) => s + (x.confidence || 0), 0) / clean.length).toFixed(3))
      : null,
  };
}

function deriveRawProxyMetrics(facts, fused) {
  const clean = facts.filter(Boolean);
  const review30 =
    weightedMean(clean, 'review_count_30d', { meituan: 1.0, dianping: 1.0, amap: 0.0 }) ??
    fused.fused_review_count ??
    0;
  const review7 =
    weightedMean(clean, 'review_count_7d', { meituan: 1.0, dianping: 1.0, amap: 0.0 }) ??
    Math.max(0, Math.round(review30 * 0.22));
  const activityDays =
    weightedMean(clean, 'review_activity_days_30d', { meituan: 1.0, dianping: 1.0, amap: 0.0 }) ??
    clamp(Math.round(review30 / 2), 0, 30);
  const ratingStd =
    weightedMean(clean, 'rating_stability_30d', { meituan: 1.0, dianping: 1.0, amap: 0.0 }) ??
    0.25;

  return {
    review_count_7d: Number(review7.toFixed(2)),
    review_count_30d: Number(review30.toFixed(2)),
    review_growth_7d_vs_30d_raw: Number(((review7 * 4) / (review30 + 1)).toFixed(4)),
    review_activity_days_30d: Number(activityDays.toFixed(2)),
    rating_stability_30d: Number(ratingStd.toFixed(4)),
  };
}

function minMaxNormalize(values) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (!Number.isFinite(min) || !Number.isFinite(max) || max === min) {
    return values.map(() => 50);
  }
  return values.map((v) => ((v - min) / (max - min)) * 100);
}

function computeDemandProxy(rowsWithRawProxy) {
  const sizeRaw = rowsWithRawProxy.map((r) => Math.log1p(r.review_count_30d || 0));
  const growthRaw = rowsWithRawProxy.map((r) => r.review_growth_7d_vs_30d_raw || 0);
  const activityRaw = rowsWithRawProxy.map((r) => clamp((r.review_activity_days_30d || 0) / 30, 0, 1));
  const stabilityRaw = rowsWithRawProxy.map((r) => r.rating_stability_30d || 0.25);

  const sizeScore = minMaxNormalize(sizeRaw);
  const growthScore = minMaxNormalize(growthRaw);
  const activityScore = minMaxNormalize(activityRaw);
  const stabilityScore = minMaxNormalize(stabilityRaw).map((v) => 100 - v);

  return rowsWithRawProxy.map((r, i) => {
    const score =
      0.45 * sizeScore[i] +
      0.3 * growthScore[i] +
      0.15 * activityScore[i] +
      0.1 * stabilityScore[i];
    const bounded = Number(clamp(score, 0, 100).toFixed(2));
    let level = 'C';
    if (bounded >= 70) level = 'A';
    else if (bounded >= 45) level = 'B';
    return {
      ...r,
      review_growth_7d_vs_30d: Number(r.review_growth_7d_vs_30d_raw.toFixed(4)),
      demand_proxy_score: bounded,
      demand_proxy_level: level,
      low_signal_flag: r.review_count_30d < 5 ? 1 : 0,
    };
  });
}

function buildQualityReport(rows) {
  const total = rows.length || 1;
  const proxyCovered = rows.filter((r) => Number.isFinite(r.demand_proxy_score)).length;
  const lowSignal = rows.filter((r) => r.low_signal_flag === 1).length;
  return {
    total_store_count: rows.length,
    proxy_coverage_rate: Number((proxyCovered / total).toFixed(4)),
    order_truth_coverage_rate: 0,
    low_signal_store_count: lowSignal,
    generated_at: new Date().toISOString(),
  };
}

async function main() {
  const args = parseArgs(process.argv);
  const poiPath = path.resolve(required(args, 'poi'));
  const outPath = path.resolve(required(args, 'output'));
  const envFile = args['env-file'] || '.env';
  const city = args.city || '';
  const perStoreDelayMs = Number(args['delay-ms'] || 280);

  loadEnvFile(envFile);

  const poiRows = parseCsv(fs.readFileSync(poiPath, 'utf8')).map((r) => ({
    name: r.name,
    category_l2: r.category_l2,
    lat: Number(r.lat),
    lng: Number(r.lng),
  }));

  const meituanRows = args['meituan-csv'] ? parseCsv(fs.readFileSync(path.resolve(args['meituan-csv']), 'utf8')) : [];
  const dianpingRows = args['dianping-csv'] ? parseCsv(fs.readFileSync(path.resolve(args['dianping-csv']), 'utf8')) : [];

  const amapKey = resolveAmapKey(args['amap-key'] || null, envFile);
  const rowsWithRawProxy = [];

  for (let i = 0; i < poiRows.length; i += 1) {
    const poi = poiRows[i];

    let amapFact = null;
    if (amapKey) {
      try {
        amapFact = await fetchAmapFact({ key: amapKey, name: poi.name, city, lat: poi.lat, lng: poi.lng });
      } catch {
        amapFact = null;
      }
    }

    const meituanFact = matchFromCsv(poi, meituanRows, 'meituan');
    const dianpingFact = matchFromCsv(poi, dianpingRows, 'dianping');
    const fused = fuseFacts([amapFact, meituanFact, dianpingFact]);
    const proxyRaw = deriveRawProxyMetrics([amapFact, meituanFact, dianpingFact], fused);

    rowsWithRawProxy.push({
      name: poi.name,
      category_l2: poi.category_l2,
      lat: poi.lat,
      lng: poi.lng,
      amap_rating: amapFact?.rating ?? '',
      amap_review_count: amapFact?.review_count ?? '',
      amap_avg_price: amapFact?.avg_price ?? '',
      meituan_rating: meituanFact?.rating ?? '',
      meituan_review_count: meituanFact?.review_count ?? '',
      meituan_order_count: '',
      meituan_avg_price: meituanFact?.avg_price ?? '',
      dianping_rating: dianpingFact?.rating ?? '',
      dianping_review_count: dianpingFact?.review_count ?? '',
      dianping_order_count: '',
      dianping_avg_price: dianpingFact?.avg_price ?? '',
      fused_rating: fused.fused_rating ?? '',
      fused_review_count: fused.fused_review_count ?? '',
      fused_order_count: '',
      data_sources: fused.data_sources,
      match_confidence: fused.match_confidence ?? '',
      order_missing_reason: fused.order_missing_reason,
      ...proxyRaw,
    });

    if (i < poiRows.length - 1) await sleep(perStoreDelayMs);
  }

  const resultRows = computeDemandProxy(rowsWithRawProxy);

  const headers = [
    'name', 'category_l2', 'lat', 'lng',
    'amap_rating', 'amap_review_count', 'amap_avg_price',
    'meituan_rating', 'meituan_review_count', 'meituan_order_count', 'meituan_avg_price',
    'dianping_rating', 'dianping_review_count', 'dianping_order_count', 'dianping_avg_price',
    'fused_rating', 'fused_review_count', 'fused_order_count', 'data_sources', 'match_confidence',
    'order_missing_reason',
    'review_count_7d', 'review_count_30d', 'review_growth_7d_vs_30d',
    'review_activity_days_30d', 'rating_stability_30d',
    'demand_proxy_score', 'demand_proxy_level', 'low_signal_flag',
  ];

  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, toCsv(headers, resultRows), 'utf8');

  const qualityPath = outPath.endsWith('.csv')
    ? `${outPath.slice(0, -4)}_quality_report.json`
    : `${outPath}_quality_report.json`;
  const qualityReport = buildQualityReport(resultRows);
  fs.writeFileSync(qualityPath, `${JSON.stringify(qualityReport, null, 2)}\n`, 'utf8');

  process.stdout.write(
    `${JSON.stringify({
      poi_count: poiRows.length,
      output: outPath,
      quality_report: qualityPath,
      used_amap: !!amapKey,
      used_meituan_csv: !!args['meituan-csv'],
      used_dianping_csv: !!args['dianping-csv'],
    })}\n`,
  );
}

if (require.main === module) {
  main().catch((err) => {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  });
}

module.exports = {
  nameSimilarity,
  deriveRawProxyMetrics,
  computeDemandProxy,
  buildQualityReport,
};
