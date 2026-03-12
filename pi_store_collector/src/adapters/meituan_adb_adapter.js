const fs = require('node:fs');
const path = require('node:path');
const { AdbClient } = require('../device/adb_client');

function decodeXmlEntities(text) {
  return String(text || '')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

function extractTextsFromUiXml(uiXml) {
  const out = [];
  const re = /text="([^"]*)"/g;
  let m;
  while ((m = re.exec(uiXml)) !== null) {
    const t = decodeXmlEntities(m[1]).trim();
    if (t) out.push(t);
  }
  return out;
}

function detectFirstNumber(text, regex) {
  const m = String(text || '').match(regex);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) ? n : null;
}

function extractStoreFactsFromTexts(texts, fallbackName) {
  let rating = null;
  let reviewCount = null;
  let monthlyOrders = null;
  let avgPrice = null;

  for (const t of texts) {
    if (rating === null) {
      const s = String(t).match(/(^|\s)([1-5]\.\d)(\s|$)/);
      if (s) {
        const n = Number(s[2]);
        if (Number.isFinite(n)) rating = n;
      }
    }
    if (reviewCount === null) {
      const c = detectFirstNumber(t, /(\d+)\s*条评价/);
      if (c !== null) reviewCount = c;
    }
    if (monthlyOrders === null) {
      const mo = detectFirstNumber(t, /月售\s*(\d+)/);
      if (mo !== null) monthlyOrders = mo;
    }
    if (avgPrice === null) {
      const ap = detectFirstNumber(t, /人均[^0-9]*([0-9]+(?:\.[0-9]+)?)/);
      if (ap !== null) avgPrice = ap;
    }
  }

  return {
    store_name: fallbackName,
    rating,
    review_count: reviewCount,
    monthly_orders: monthlyOrders,
    avg_price: avgPrice,
  };
}

function extractProductFactsFromTexts(texts, limit = 20) {
  const rows = [];
  for (let i = 0; i < texts.length; i += 1) {
    const t = texts[i];
    const priceMatch = t.match(/¥\s*([0-9]+(?:\.[0-9]+)?)/);
    if (!priceMatch) continue;
    const name = (texts[i - 1] || '').trim();
    if (!name || /月售|评价|人均|搜索|筛选/.test(name)) continue;

    rows.push({
      product_id: '',
      product_name: name.slice(0, 64),
      price: Number(priceMatch[1]),
      sales_text: texts[i + 1] && /月售/.test(texts[i + 1]) ? texts[i + 1].slice(0, 64) : '',
      rank_no: rows.length + 1,
    });
    if (rows.length >= limit) break;
  }
  return rows;
}

function extractReviewFactsFromTexts(texts, limit = 30) {
  const rows = [];
  for (const t of texts) {
    if (t.length < 8 || t.length > 140) continue;
    if (/月售|人均|搜索|筛选|评价\(|全部评价|店铺/.test(t)) continue;
    if (/^[0-9.\-:\/\s]+$/.test(t)) continue;
    rows.push({
      review_id: '',
      rating: null,
      content: t,
      like_count: null,
      comment_time: '',
    });
    if (rows.length >= limit) break;
  }
  return rows;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function tsId() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

function createMeituanAdbAdapter(options = {}) {
  const artifactRoot = options.artifactRoot || path.resolve(__dirname, '..', '..', 'data', 'artifacts');
  const packageName = options.packageName || 'com.sankuai.meituan';
  const searchTapX = Number(options.searchTapX || 540);
  const searchTapY = Number(options.searchTapY || 180);
  const enterKeyCode = Number(options.enterKeyCode || 66);
  const launchWaitMs = Number(options.launchWaitMs || 1800);
  const inputWaitMs = Number(options.inputWaitMs || 500);
  const resultWaitMs = Number(options.resultWaitMs || 2000);

  return {
    name: 'meituan-adb-adapter',
    async collect(task, context = {}) {
      const deviceId = context.deviceId || '';
      const adb = new AdbClient({ serial: deviceId, runner: options.runner || null });
      const storeName = task?.store_name || '';
      const taskId = task?.task_id || `task_${tsId()}`;
      const runDir = path.join(artifactRoot, 'meituan', taskId);
      ensureDir(runDir);

      await adb.launchApp(packageName);
      await adb.wait(launchWaitMs);
      await adb.tap(searchTapX, searchTapY);
      await adb.wait(inputWaitMs);
      await adb.inputText(storeName);
      await adb.keyevent(enterKeyCode);
      await adb.wait(resultWaitMs);

      const uiXml = await adb.dumpUiXml('/sdcard/pi_store_collector_meituan_ui.xml');
      const uiPath = path.join(runDir, `ui_dump_${tsId()}.xml`);
      fs.writeFileSync(uiPath, uiXml, 'utf8');

      const texts = extractTextsFromUiXml(uiXml);
      const storeFacts = extractStoreFactsFromTexts(texts, storeName);
      const productFacts = extractProductFactsFromTexts(texts, Number(task?.product_limit || 20));
      const reviewFacts = extractReviewFactsFromTexts(texts, Number(task?.review_limit || 30));
      const now = new Date().toISOString();

      return {
        store_facts: {
          platform_store_id: '',
          store_name: storeFacts.store_name,
          lat: task?.lat ?? null,
          lng: task?.lng ?? null,
          rating: storeFacts.rating,
          review_count: storeFacts.review_count,
          monthly_orders: storeFacts.monthly_orders,
          avg_price: storeFacts.avg_price,
          crawl_time: now,
          confidence: 0.4,
        },
        product_facts: productFacts.map((x) => ({ ...x, crawl_time: now })),
        review_facts: reviewFacts.map((x) => ({ ...x, crawl_time: now })),
        raw_artifact_path: uiPath,
      };
    },
  };
}

module.exports = {
  createMeituanAdbAdapter,
  extractTextsFromUiXml,
  extractStoreFactsFromTexts,
  extractProductFactsFromTexts,
  extractReviewFactsFromTexts,
};
