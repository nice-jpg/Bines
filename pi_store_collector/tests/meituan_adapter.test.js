const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { escapeForAdbInputText } = require('../src/device/adb_client');

const {
  createMeituanAdbAdapter,
  extractTextsFromUiXml,
  extractStoreFactsFromTexts,
  extractProductFactsFromTexts,
  extractReviewFactsFromTexts,
} = require('../src/adapters/meituan_adb_adapter');
const { buildAdapters } = require('../src/adapters');

test('escapeForAdbInputText escapes shell-sensitive store names', () => {
  const out = escapeForAdbInputText('三陶自习室(一中校区)');
  assert.equal(out, '三陶自习室\\(一中校区\\)');

  const out2 = escapeForAdbInputText('A 店 & B 店');
  assert.equal(out2, 'A%s店%s\\&%sB%s店');
});

test('extractors parse ui xml text and basic facts', () => {
  const xml = `<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
  <hierarchy>
    <node text="爱萌宠物店"/>
    <node text="4.6"/>
    <node text="123条评价"/>
    <node text="月售 88"/>
    <node text="人均 ¥35"/>
    <node text="招牌猫粮"/>
    <node text="¥29.9"/>
    <node text="月售50"/>
    <node text="服务挺好，洗护很认真"/>
  </hierarchy>`;
  const texts = extractTextsFromUiXml(xml);
  assert.ok(texts.includes('爱萌宠物店'));

  const store = extractStoreFactsFromTexts(texts, '爱萌宠物店');
  assert.equal(store.rating, 4.6);
  assert.equal(store.review_count, 123);
  assert.equal(store.monthly_orders, 88);
  assert.equal(store.avg_price, 35);

  const products = extractProductFactsFromTexts(texts, 5);
  assert.ok(products.length >= 1);
  assert.equal(products[0].product_name, '招牌猫粮');

  const reviews = extractReviewFactsFromTexts(texts, 5);
  assert.ok(reviews.find((x) => x.content.includes('服务挺好')));
});

test('meituan adb adapter collect with mocked adb runner', async () => {
  const calls = [];
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'meituan-adapter-'));
  const xml = `<hierarchy><node text="测试羊肉馆"/><node text="4.5"/><node text="88条评价"/><node text="羊肉汤"/><node text="¥26"/></hierarchy>`;
  let dumpsysCount = 0;

  async function runner(bin, args) {
    calls.push({ bin, args });
    if (args.includes('dumpsys')) {
      dumpsysCount += 1;
      if (dumpsysCount === 1) {
        return {
          stdout: 'View Hierarchy:\n  android.widget.TextView{id/search V.ED.... 100,200-300,260 text="搜索"}\n',
          stderr: '',
        };
      }
      return {
        stdout: 'View Hierarchy:\n  android.widget.TextView{id/store V.ED.... 100,200-300,260 text="测试羊肉馆"}\n  android.widget.TextView{id/rating V.ED.... 100,280-180,320 text="4.5"}\n  android.widget.TextView{id/review V.ED.... 100,330-260,360 text="88条评价"}\n  android.widget.TextView{id/product V.ED.... 100,400-220,440 text="羊肉汤"}\n  android.widget.TextView{id/price V.ED.... 240,400-320,440 text="¥26"}\n',
        stderr: '',
      };
    }
    if (args.includes('uiautomator') && args.includes('dump')) {
      throw new Error('uiautomator unavailable');
    }
    if (args.includes('exec-out') && args.includes('cat')) {
      return { stdout: xml, stderr: '' };
    }
    return { stdout: '', stderr: '' };
  }

  const adapter = createMeituanAdbAdapter({
    artifactRoot: tmpDir,
    runner,
    launchWaitMs: 1,
    inputWaitMs: 1,
    resultWaitMs: 1,
  });
  const out = await adapter.collect({
    task_id: 't_mei_1',
    store_name: '测试羊肉馆',
    lat: 33.64,
    lng: 116.96,
    product_limit: 10,
    review_limit: 10,
  }, { deviceId: 'android-01' });

  assert.equal(out.store_facts.store_name, '测试羊肉馆');
  assert.equal(out.store_facts.rating, 4.5);
  assert.equal(out.store_facts.review_count, 88);
  assert.ok(Array.isArray(out.product_facts));
  assert.ok(fs.existsSync(out.raw_artifact_path));
  assert.ok(calls.length >= 5);
  const searchTapCall = calls.find((x) => x.args.includes('tap') && x.args.includes('200') && x.args.includes('230'));
  assert.ok(searchTapCall);
});

test('buildAdapters switches meituan adapter by config', () => {
  const a1 = buildAdapters({ useMock: false });
  assert.equal(a1.meituan.name, 'meituan-adb-adapter');

  const a2 = buildAdapters({ useMock: false, useMeituanAdb: true, meituanRunner: async () => ({ stdout: '', stderr: '' }) });
  assert.equal(a2.meituan.name, 'meituan-adb-adapter');
});
