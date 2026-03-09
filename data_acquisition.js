const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_RADIUS_KM = 1.5;
const MIN_RADIUS_KM = 0.8;
const MAX_RADIUS_KM = 2.0;
const SUPPORTED_SOURCES = ['amap', 'osm'];

const PRICE_BAND_BY_L2 = {
  中式快餐: 2, 咖啡馆: 3, 面包甜点: 2, 便利店: 1, 服饰店: 3, 美妆集合店: 3,
  理发店: 2, 美甲店: 2, 洗衣店: 2, 编程培训: 4, 语言培训: 4, 素质教育: 4,
  口腔门诊: 4, 康复理疗: 3, 健康管理: 3, 桌游馆: 3, 剧本杀: 3, 健身工作室: 3,
};

const TAG_TO_L2 = {
  'amenity|fast_food': '中式快餐',
  'amenity|restaurant': '中式快餐',
  'amenity|cafe': '咖啡馆',
  'shop|bakery': '面包甜点',
  'shop|convenience': '便利店',
  'shop|clothes': '服饰店',
  'shop|beauty': '美妆集合店',
  'shop|cosmetics': '美妆集合店',
  'shop|hairdresser': '理发店',
  'shop|laundry': '洗衣店',
  'amenity|language_school': '语言培训',
  'amenity|school': '素质教育',
  'amenity|college': '素质教育',
  'amenity|dentist': '口腔门诊',
  'amenity|clinic': '健康管理',
  'healthcare|rehabilitation': '康复理疗',
  'leisure|sports_centre': '健身工作室',
  'leisure|fitness_centre': '健身工作室',
  'leisure|escape_game': '剧本杀',
};

const AMAP_MAIN_TYPES = '050000|060000|070000|080000|090000|140000';

function clamp(v, low, high) {
  return Math.max(low, Math.min(high, v));
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

async function httpGetJson(url, headers = {}, timeoutMs = 25000) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { headers, signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(t);
  }
}

async function httpPostJson(url, data, headers = { 'Content-Type': 'text/plain' }, timeoutMs = 45000) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { method: 'POST', headers, body: data, signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(t);
  }
}

function normalizeGeoText(text) {
  return String(text || '').replace(/\s+/g, '').toLowerCase();
}

function extractQueryTokens(query) {
  return normalizeGeoText(query).split(/[省市区县州盟旗,\s]+/).filter(Boolean);
}

function selectBestGeocodeCandidate(query, candidates) {
  const normalizedQuery = normalizeGeoText(query);
  const tokens = extractQueryTokens(query);
  let best = null;
  let bestScore = -1;

  for (const item of candidates) {
    const display = normalizeGeoText(item.display_name || '');
    let score = 0;
    if (normalizedQuery && display.includes(normalizedQuery)) score += 10;
    score += tokens.filter((t) => display.includes(t)).length * 2;
    const type = String(item.type || '').toLowerCase();
    if (['city', 'town', 'administrative'].includes(type)) score += 1;
    const name = normalizeGeoText(item.name || '');
    if (name && tokens.some((t) => name.includes(t))) score += 1.5;
    score += Number(item.importance || 0) * 0.01;
    if (score > bestScore) {
      best = item;
      bestScore = score;
    }
  }

  return best || candidates[0];
}

async function geocodeRegionOsm(query, countrycodes) {
  const params = new URLSearchParams({ q: query, format: 'jsonv2', limit: '8' });
  if (countrycodes) params.set('countrycodes', countrycodes);
  const payload = await httpGetJson(`https://nominatim.openstreetmap.org/search?${params.toString()}`, {
    'User-Agent': 'market-analysis-mcp/0.1',
    Accept: 'application/json',
  });
  if (!payload || !payload.length) throw new Error(`无法解析地理位置: ${query}`);
  const top = selectBestGeocodeCandidate(query, payload);
  return { display_name: top.display_name, lat: Number(top.lat), lng: Number(top.lon) };
}

async function geocodeRegionAmap(query, amapKey) {
  const params = new URLSearchParams({ address: query, key: amapKey });
  const payload = await httpGetJson(`https://restapi.amap.com/v3/geocode/geo?${params.toString()}`);
  if (payload.status !== '1' || !payload.geocodes || !payload.geocodes.length) {
    throw new Error(`高德地理编码失败: ${payload.info || 'unknown error'}`);
  }
  const top = payload.geocodes[0];
  const [lng, lat] = String(top.location).split(',').map(Number);
  const display = `${top.city || top.province || ''}${top.district || ''}`.trim() || query;
  return { display_name: display, lat, lng };
}

function buildOverpassQuery(lat, lng, radiusM) {
  return `
[out:json][timeout:45];
(
  node(around:${radiusM},${lat},${lng})["amenity"];
  way(around:${radiusM},${lat},${lng})["amenity"];
  relation(around:${radiusM},${lat},${lng})["amenity"];
  node(around:${radiusM},${lat},${lng})["shop"];
  way(around:${radiusM},${lat},${lng})["shop"];
  relation(around:${radiusM},${lat},${lng})["shop"];
  node(around:${radiusM},${lat},${lng})["leisure"];
  way(around:${radiusM},${lat},${lng})["leisure"];
  relation(around:${radiusM},${lat},${lng})["leisure"];
  node(around:${radiusM},${lat},${lng})["office"];
  way(around:${radiusM},${lat},${lng})["office"];
  relation(around:${radiusM},${lat},${lng})["office"];
  node(around:${radiusM},${lat},${lng})["building"];
  way(around:${radiusM},${lat},${lng})["building"];
  relation(around:${radiusM},${lat},${lng})["building"];
  node(around:${radiusM},${lat},${lng})["public_transport"];
  node(around:${radiusM},${lat},${lng})["railway"="station"];
  node(around:${radiusM},${lat},${lng})["highway"="bus_stop"];
);
out center tags;
`;
}

async function fetchOverpass(lat, lng, radiusKm) {
  const payload = await httpPostJson('https://overpass-api.de/api/interpreter', buildOverpassQuery(lat, lng, Math.floor(radiusKm * 1000)));
  return payload.elements || [];
}

async function fetchAmapPois(lat, lng, radiusKm, amapKey, maxPages = 8) {
  const rows = [];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  for (let page = 1; page <= maxPages; page += 1) {
    const params = new URLSearchParams({
      key: amapKey,
      location: `${lng},${lat}`,
      radius: String(Math.floor(radiusKm * 1000)),
      types: AMAP_MAIN_TYPES,
      offset: '25',
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
    rows.push(...pois);
    if (pois.length < 25) break;
    // 主动限速，降低连续翻页触发 QPS 限流概率。
    await sleep(800);
  }
  return rows;
}

function inferCategoryL2(tags) {
  const keys = [
    ['amenity', tags.amenity],
    ['shop', tags.shop],
    ['leisure', tags.leisure],
  ];
  for (const [k, v] of keys) {
    const l2 = TAG_TO_L2[`${k}|${v}`];
    if (l2) return l2;
  }
  if ((tags.amenity || '').includes('school')) return '素质教育';
  if (tags.healthcare) return '健康管理';
  if (tags.office) return '健康管理';
  return null;
}

function inferCategoryL2FromAmap(poiType) {
  const typeText = String(poiType || '').trim();
  if (!typeText) return null;

  const segments = typeText.split(';').map((x) => x.trim()).filter(Boolean);
  const full = segments.join(';');
  const top = segments[0] || '';

  const preciseRules = [
    ['餐饮服务;咖啡厅', '咖啡馆'],
    ['餐饮服务;糕饼店', '面包甜点'],
    ['餐饮服务;快餐厅', '中式快餐'],
    ['购物服务;便利店', '便利店'],
    ['购物服务;专卖店;服装鞋帽皮具店', '服饰店'],
    ['购物服务;专卖店;宠物用品店', '生活服务'],
    ['购物服务;商场;化妆品店', '美妆集合店'],
    ['生活服务;美容美发店;理发店', '理发店'],
    ['生活服务;美容美发店;美甲', '美甲店'],
    ['生活服务;洗浴推拿场所;洗衣店', '洗衣店'],
    ['科教文化服务;培训机构', '语言培训'],
    ['科教文化服务;学校', '素质教育'],
    ['医疗保健服务;专科医院;口腔医院', '口腔门诊'],
    ['医疗保健服务;诊所', '健康管理'],
    ['医疗保健服务;疗养院', '康复理疗'],
    ['体育休闲服务;运动场馆;健身中心', '健身工作室'],
    ['体育休闲服务;娱乐场所;游戏厅', '桌游馆'],
  ];
  for (const [needle, mapped] of preciseRules) {
    if (full.includes(needle)) return mapped;
  }

  const keywordRules = [
    ['咖啡', '咖啡馆'],
    ['甜品', '面包甜点'],
    ['面包', '面包甜点'],
    ['快餐', '中式快餐'],
    ['便利店', '便利店'],
    ['服装', '服饰店'],
    ['化妆品', '美妆集合店'],
    ['理发', '理发店'],
    ['美甲', '美甲店'],
    ['洗衣', '洗衣店'],
    ['宠物', '生活服务'],
    ['培训', '语言培训'],
    ['学校', '素质教育'],
    ['口腔', '口腔门诊'],
    ['诊所', '健康管理'],
    ['康复', '康复理疗'],
    ['健身', '健身工作室'],
    ['桌游', '桌游馆'],
    ['剧本杀', '剧本杀'],
  ];
  for (const [needle, mapped] of keywordRules) {
    if (full.includes(needle)) return mapped;
  }

  if (top.includes('购物服务')) return '生活服务';
  if (top.includes('生活服务')) return '生活服务';
  if (top.includes('医疗保健服务')) return '健康管理';
  if (top.includes('科教文化服务')) return '素质教育';
  if (top.includes('体育休闲服务')) return '文娱';
  if (top.includes('餐饮服务')) return '中式快餐';
  return null;
}

function getLatLngFromElement(e) {
  if (Number.isFinite(e.lat) && Number.isFinite(e.lon)) return [Number(e.lat), Number(e.lon)];
  if (e.center && Number.isFinite(e.center.lat) && Number.isFinite(e.center.lon)) return [Number(e.center.lat), Number(e.center.lon)];
  return null;
}

function inferOpenStatus(tags) {
  return tags.disused === 'yes' || tags.abandoned === 'yes' ? 'closed' : 'open';
}

function inferPopularity(tags) {
  let score = 10;
  if (tags.brand) score += 15;
  if (tags.opening_hours) score += 10;
  if (tags.website || tags['contact:website']) score += 10;
  if (tags.phone || tags['contact:phone']) score += 8;
  if (tags['addr:housenumber']) score += 5;
  return Math.floor(clamp(score * 2.2, 8, 220));
}

function inferRating(tags, categoryL2) {
  let base = 4.1;
  if (['咖啡馆', '理发店', '口腔门诊'].includes(categoryL2)) base += 0.15;
  if (tags.brand) base += 0.1;
  if (tags.opening_hours) base += 0.05;
  return Math.round(clamp(base, 3.7, 4.8) * 100) / 100;
}

function dedupPoi(rows) {
  const m = new Map();
  for (const r of rows) {
    const key = `${r.name}|${r.category_l2}|${r.lat}|${r.lng}`;
    const cur = m.get(key);
    if (!cur || r.review_count > cur.review_count) m.set(key, r);
  }
  return [...m.values()];
}

function buildPoiSnapshot(elements, centerLat, centerLng, radiusKm) {
  const rows = [];
  for (const e of elements) {
    const tags = e.tags || {};
    const categoryL2 = inferCategoryL2(tags);
    if (!categoryL2) continue;
    const point = getLatLngFromElement(e);
    if (!point) continue;
    const [lat, lng] = point;
    if (haversineKm(centerLat, centerLng, lat, lng) > radiusKm) continue;
    rows.push({
      name: tags.name || `${categoryL2}_${e.type || 'node'}_${e.id || 'x'}`,
      category_l2: categoryL2,
      lat: Number(lat.toFixed(6)),
      lng: Number(lng.toFixed(6)),
      rating: inferRating(tags, categoryL2),
      review_count: inferPopularity(tags),
      price_band: PRICE_BAND_BY_L2[categoryL2] || 2,
      open_status: inferOpenStatus(tags),
    });
  }
  return dedupPoi(rows);
}

function buildPoiSnapshotFromAmap(pois, centerLat, centerLng, radiusKm) {
  const rows = [];
  for (const p of pois) {
    const loc = String(p.location || '');
    if (!loc.includes(',')) continue;
    const [lngStr, latStr] = loc.split(',');
    const lat = Number(latStr);
    const lng = Number(lngStr);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
    if (haversineKm(centerLat, centerLng, lat, lng) > radiusKm) continue;
    const categoryL2 = inferCategoryL2FromAmap(p.type);
    if (!categoryL2) continue;
    const biz = p.biz_ext || {};
    const ratingRaw = Number(biz.rating || 4.1);
    const cost = Number(biz.cost || 0);
    let priceBand = PRICE_BAND_BY_L2[categoryL2] || 2;
    if (Number.isFinite(cost) && cost > 0) {
      if (cost < 30) priceBand = 1;
      else if (cost < 80) priceBand = 2;
      else if (cost < 160) priceBand = 3;
      else priceBand = 4;
    }
    rows.push({
      name: p.name || `${categoryL2}_${p.id || 'x'}`,
      category_l2: categoryL2,
      lat: Number(lat.toFixed(6)),
      lng: Number(lng.toFixed(6)),
      rating: Math.round(clamp(ratingRaw, 3.5, 5.0) * 100) / 100,
      review_count: Math.floor(clamp(Number(p.importance || 30), 8, 220)),
      price_band: priceBand,
      open_status: 'open',
    });
  }
  return dedupPoi(rows);
}

function countTags(elements, key, value = null) {
  let n = 0;
  for (const e of elements) {
    const tags = e.tags || {};
    if (!(key in tags)) continue;
    if (value == null || tags[key] === value) n += 1;
  }
  return n;
}

function suggestRadiusKm(centerLat, centerLng, poiRows, fallbackKm) {
  if (poiRows.length < 15) return fallbackKm;
  const dists = poiRows.map((r) => haversineKm(centerLat, centerLng, r.lat, r.lng)).sort((a, b) => a - b);
  const q75Idx = Math.floor(0.75 * (dists.length - 1));
  const radius = dists[q75Idx] * 1.15;
  return Number(clamp(radius, MIN_RADIUS_KM, MAX_RADIUS_KM).toFixed(2));
}

function buildRegionContext(elements, poiRows) {
  const residential = countTags(elements, 'building', 'residential') + countTags(elements, 'landuse', 'residential');
  const office = countTags(elements, 'office') + countTags(elements, 'building', 'commercial');
  const transit = countTags(elements, 'public_transport') + countTags(elements, 'railway', 'station') + countTags(elements, 'highway', 'bus_stop');
  const commercialPoi = poiRows.filter((r) => r.open_status === 'open').length;

  const officeResRatio = (office + 1) / (residential + 1);
  const dayNight = clamp(35 + commercialPoi * 0.7 + officeResRatio * 12, 20, 95);
  const accessibility = clamp(30 + transit * 4.2 + Math.log1p(commercialPoi) * 8, 15, 95);
  const rentProxy = clamp(25 + commercialPoi * 0.5 + accessibility * 0.35 + officeResRatio * 8, 20, 95);

  return {
    day_night_population_proxy: Number(dayNight.toFixed(2)),
    office_residential_ratio: Number(clamp(officeResRatio, 0.2, 3.0).toFixed(2)),
    accessibility_proxy: Number(accessibility.toFixed(2)),
    rent_proxy: Number(rentProxy.toFixed(2)),
  };
}

function buildRegionContextFromPois(poiRows) {
  const openRows = poiRows.filter((r) => r.open_status === 'open');
  const total = Math.max(1, openRows.length);
  const service = openRows.filter((r) => ['理发店', '美甲店', '洗衣店', '健康管理'].includes(r.category_l2)).length;
  const eduMed = openRows.filter((r) => ['语言培训', '素质教育', '口腔门诊', '康复理疗'].includes(r.category_l2)).length;
  const leisure = openRows.filter((r) => ['桌游馆', '剧本杀', '健身工作室'].includes(r.category_l2)).length;
  const avgBand = openRows.reduce((s, r) => s + r.price_band, 0) / total;

  const officeResRatio = clamp((eduMed + 1) / (service + 1), 0.2, 3.0);
  const dayNight = clamp(35 + total * 0.6 + leisure * 1.2, 20, 95);
  const accessibility = clamp(30 + Math.log1p(total) * 12 + leisure * 0.8, 15, 95);
  const rentProxy = clamp(20 + avgBand * 12 + accessibility * 0.4, 20, 95);

  return {
    day_night_population_proxy: Number(dayNight.toFixed(2)),
    office_residential_ratio: Number(officeResRatio.toFixed(2)),
    accessibility_proxy: Number(accessibility.toFixed(2)),
    rent_proxy: Number(rentProxy.toFixed(2)),
  };
}

function writePoiSnapshot(filePath, poiRows) {
  const headers = ['name', 'category_l2', 'lat', 'lng', 'rating', 'review_count', 'price_band', 'open_status'];
  const lines = [headers.join(',')];
  for (const r of poiRows) lines.push(headers.map((h) => String(r[h] ?? '')).join(','));
  fs.writeFileSync(filePath, `${lines.join('\n')}\n`, 'utf8');
}

function writeJson(filePath, payload) {
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2), 'utf8');
}

async function acquireMarketInputs({
  regionQuery,
  outputDir,
  defaultRadiusKm = DEFAULT_RADIUS_KM,
  countrycodes = null,
  dataSource = 'amap',
  amapKey = null,
  amapMaxPages = 8,
  emitProgress = null,
}) {
  const source = String(dataSource || 'amap').toLowerCase();
  if (!SUPPORTED_SOURCES.includes(source)) throw new Error(`不支持的数据源: ${source}, 可选: ${SUPPORTED_SOURCES.join(',')}`);

  fs.mkdirSync(outputDir, { recursive: true });
  if (emitProgress) emitProgress('geocoding', `解析地理位置: ${regionQuery} (source=${source})`);

  let center;
  let poiRows;
  let elements = [];

  if (source === 'amap') {
    const key = amapKey || process.env.AMAP_API_KEY;
    if (!key) throw new Error('使用高德数据源需要传入 amap_key 或设置环境变量 AMAP_API_KEY');
    center = await geocodeRegionAmap(regionQuery, key);
    if (emitProgress) emitProgress('poi_fetch', '抓取高德 POI 数据');
    const pois = await fetchAmapPois(center.lat, center.lng, defaultRadiusKm, key, Number(amapMaxPages || 8));
    poiRows = buildPoiSnapshotFromAmap(pois, center.lat, center.lng, defaultRadiusKm);
  } else {
    center = await geocodeRegionOsm(regionQuery, countrycodes);
    if (emitProgress) emitProgress('poi_fetch', '抓取 OSM POI/区域特征');
    elements = await fetchOverpass(center.lat, center.lng, defaultRadiusKm);
    poiRows = buildPoiSnapshot(elements, center.lat, center.lng, defaultRadiusKm);
  }

  if (poiRows.length < 20) {
    await new Promise((r) => setTimeout(r, 1000));
    const expanded = clamp(defaultRadiusKm + 0.4, MIN_RADIUS_KM, MAX_RADIUS_KM);
    if (source === 'amap') {
      const key = amapKey || process.env.AMAP_API_KEY;
      const pois = await fetchAmapPois(center.lat, center.lng, expanded, key, Number(amapMaxPages || 8));
      poiRows = buildPoiSnapshotFromAmap(pois, center.lat, center.lng, expanded);
    } else {
      elements = await fetchOverpass(center.lat, center.lng, expanded);
      poiRows = buildPoiSnapshot(elements, center.lat, center.lng, expanded);
    }
    defaultRadiusKm = expanded;
  }

  if (emitProgress) emitProgress('derive_context', '生成 context 与建议半径');
  if (!poiRows.length) throw new Error('未获取到有效 POI，请更换区域关键词或稍后重试。');

  const suggestedRadiusKm = suggestRadiusKm(center.lat, center.lng, poiRows, defaultRadiusKm);
  const regionContext = source === 'amap' ? buildRegionContextFromPois(poiRows) : buildRegionContext(elements, poiRows);

  if (emitProgress) emitProgress('write_outputs', '写入输入文件');
  const poiPath = path.join(outputDir, 'poi_snapshot.csv');
  const contextPath = path.join(outputDir, 'region_context.json');
  writePoiSnapshot(poiPath, poiRows);
  writeJson(contextPath, regionContext);

  return {
    region_query: regionQuery,
    resolved_location: center.display_name,
    data_source: source,
    center_lat: center.lat,
    center_lng: center.lng,
    radius_km: suggestedRadiusKm,
    poi_count: poiRows.length,
    poi_path: poiPath,
    context_path: contextPath,
  };
}

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

async function main() {
  const args = parseArgs(process.argv);
  if (!args['region-query'] || !args['output-dir']) {
    console.error('Usage: node data_acquisition.js --region-query "上海 徐家汇" --output-dir data/runtime_inputs [--data-source amap|osm] [--amap-key xxx] [--amap-max-pages 8] [--countrycodes cn]');
    process.exit(1);
  }

  const result = await acquireMarketInputs({
    regionQuery: args['region-query'],
    outputDir: args['output-dir'],
    defaultRadiusKm: Number(args['default-radius-km'] || DEFAULT_RADIUS_KM),
    countrycodes: args.countrycodes || null,
    dataSource: args['data-source'] || 'amap',
    amapKey: args['amap-key'] || null,
    amapMaxPages: Number(args['amap-max-pages'] || 8),
  });
  console.log(JSON.stringify(result, null, 2));
}

if (require.main === module) {
  main().catch((err) => {
    console.error(err.message || String(err));
    process.exit(1);
  });
}

module.exports = {
  DEFAULT_RADIUS_KM,
  MIN_RADIUS_KM,
  MAX_RADIUS_KM,
  SUPPORTED_SOURCES,
  clamp,
  haversineKm,
  selectBestGeocodeCandidate,
  buildPoiSnapshot,
  buildPoiSnapshotFromAmap,
  buildRegionContext,
  buildRegionContextFromPois,
  suggestRadiusKm,
  acquireMarketInputs,
};
