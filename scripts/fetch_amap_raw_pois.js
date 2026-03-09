#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');

const AMAP_MAIN_TYPES = '050000|060000|070000|080000|090000|140000';

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

function requireArg(args, key) {
  if (!args[key]) throw new Error(`Missing required arg --${key}`);
  return args[key];
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
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

async function geocode(address, key) {
  const params = new URLSearchParams({ address, key });
  const payload = await httpGetJson(`https://restapi.amap.com/v3/geocode/geo?${params.toString()}`);
  if (payload.status !== '1' || !payload.geocodes || !payload.geocodes.length) {
    throw new Error(`高德地理编码失败: ${payload.info || 'unknown error'}`);
  }
  const top = payload.geocodes[0];
  const [lng, lat] = String(top.location).split(',').map(Number);
  return {
    resolved_location: `${top.city || top.province || ''}${top.district || ''}`.trim() || address,
    center_lat: lat,
    center_lng: lng,
  };
}

async function fetchAmapRawPois({ key, lat, lng, radiusKm, maxPages, pageSize, totalLimit }) {
  const out = [];
  const cappedPageSize = Math.max(1, Math.min(25, Number(pageSize) || 25));
  const cappedLimit = Math.max(1, Number(totalLimit) || 300);
  const pagesByLimit = Math.ceil(cappedLimit / cappedPageSize);
  const effectivePages = Math.max(1, Math.min(Number(maxPages) || 12, pagesByLimit));

  for (let page = 1; page <= effectivePages; page += 1) {
    const params = new URLSearchParams({
      key,
      location: `${lng},${lat}`,
      radius: String(Math.floor(radiusKm * 1000)),
      types: AMAP_MAIN_TYPES,
      offset: String(cappedPageSize),
      page: String(page),
      sortrule: 'distance',
      extensions: 'all',
    });

    let payload = null;
    let lastErr = null;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        payload = await httpGetJson(`https://restapi.amap.com/v3/place/around?${params.toString()}`);
        if (payload.status === '1') break;
        lastErr = payload.info || 'unknown error';
        if (String(lastErr).includes('CUQPS_HAS_EXCEEDED_THE_LIMIT')) {
          await sleep(1200 * attempt);
          continue;
        }
        throw new Error(`高德POI检索失败: ${lastErr}`);
      } catch (err) {
        lastErr = err.message || String(err);
        if (attempt < 3) await sleep(1200 * attempt);
      }
    }
    if (!payload || payload.status !== '1') throw new Error(`高德POI检索失败: ${lastErr || 'unknown error'}`);

    const pois = payload.pois || [];
    out.push(...pois);
    if (out.length >= cappedLimit) break;
    if (pois.length < cappedPageSize) break;
    await sleep(800);
  }

  return out.slice(0, cappedLimit);
}

async function main() {
  const args = parseArgs(process.argv);
  const key = args['amap-key'] || process.env.AMAP_API_KEY;
  if (!key) throw new Error('请提供 --amap-key 或设置 AMAP_API_KEY');

  const regionQuery = requireArg(args, 'region-query');
  const outputFile = requireArg(args, 'output-file');
  const radiusKm = Number(args['radius-km'] || 1.5);
  const pageSize = Number(args['page-size'] || 25);
  const maxPages = Number(args['max-pages'] || 12);
  const totalLimit = Number(args['total-limit'] || 300);

  const geo = await geocode(regionQuery, key);
  const pois = await fetchAmapRawPois({
    key,
    lat: geo.center_lat,
    lng: geo.center_lng,
    radiusKm,
    maxPages,
    pageSize,
    totalLimit,
  });

  const payload = {
    region_query: regionQuery,
    resolved_location: geo.resolved_location,
    center_lat: geo.center_lat,
    center_lng: geo.center_lng,
    radius_km: radiusKm,
    fetched_count: pois.length,
    page_size: pageSize,
    max_pages: maxPages,
    total_limit: totalLimit,
    pois,
  };

  fs.mkdirSync(path.dirname(path.resolve(outputFile)), { recursive: true });
  fs.writeFileSync(path.resolve(outputFile), JSON.stringify(payload, null, 2), 'utf8');
  process.stdout.write(`${JSON.stringify({ output_file: path.resolve(outputFile), fetched_count: pois.length })}\n`);
}

if (require.main === module) {
  main().catch((err) => {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  });
}
