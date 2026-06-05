const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const {
  appendLayerToHierarchy,
  buildOcrLayer,
  buildSyntheticHierarchy,
  dumpCurrentPageXml,
  parseArgs,
} = require('../scripts/dump_current_page_xml');

test('parseArgs parses xml dump cli flags', () => {
  assert.deepEqual(
    parseArgs([
      'node',
      'dump_current_page_xml.js',
      '--device-id',
      'device-1',
      '--output',
      'out/page.xml',
      '--verbose',
    ]),
    {
      'device-id': 'device-1',
      output: 'out/page.xml',
      verbose: 'true',
    },
  );
});

test('buildOcrLayer appends visible OCR text nodes to hierarchy', () => {
  const layer = buildOcrLayer([
    { text: '附近美食', confidence: 0.92, x1: 10, y1: 20, x2: 110, y2: 60 },
    { text: 'A&B <C>', confidence: 0.8, x1: 12, y1: 70, x2: 120, y2: 100 },
  ], { screenshotPath: '/tmp/screen.png' });
  const xml = appendLayerToHierarchy('<hierarchy rotation="0"><node /></hierarchy>', layer);

  assert.match(xml, /<ocr-layer source="macos-vision" screenshot="\/tmp\/screen\.png">/);
  assert.match(xml, /<ocr-node index="0" text="附近美食" confidence="0\.9200" bounds="\[10,20\]\[110,60\]" \/>/);
  assert.match(xml, /text="A&amp;B &lt;C&gt;"/);
  assert.ok(xml.endsWith('</hierarchy>'));
});

test('buildSyntheticHierarchy escapes dump errors', () => {
  assert.equal(
    buildSyntheticHierarchy({ reason: 'uiautomator <failed> & "bad"' }),
    '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0" source="synthetic-ocr" dump-error="uiautomator &lt;failed&gt; &amp; &quot;bad&quot;"></hierarchy>',
  );
});

test('dumpCurrentPageXml writes current hierarchy to output file', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'page-xml-'));
  const output = path.join(tmpDir, 'nested', 'page.xml');
  const calls = [];
  const adb = {
    dumpUiXml: async (devicePath) => {
      calls.push(devicePath);
      return '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0" />';
    },
  };

  try {
    const xml = await dumpCurrentPageXml({
      adb,
      output,
      deviceDumpPath: '/sdcard/custom.xml',
    });

    assert.equal(xml, '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0" />');
    assert.deepEqual(calls, ['/sdcard/custom.xml']);
    assert.equal(fs.readFileSync(output, 'utf8'), `${xml}\n`);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('dumpCurrentPageXml can augment hierarchy with OCR screenshot text', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'page-xml-ocr-test-'));
  const screenshotOutput = path.join(tmpDir, 'screen.png');
  const adb = {
    dumpUiXml: async () => '<hierarchy rotation="0"></hierarchy>',
  };

  try {
    const xml = await dumpCurrentPageXml({
      adb,
      augmentOcr: true,
      screenshotOutput,
      captureScreenshotFn: async ({ outputPath }) => {
        fs.writeFileSync(outputPath, 'png');
      },
      recognizeScreenshotTextFn: async ({ imagePath }) => {
        assert.equal(imagePath, screenshotOutput);
        return [{ text: '页面标题', confidence: 0.99, x1: 1, y1: 2, x2: 101, y2: 42 }];
      },
    });

    assert.match(xml, /<ocr-layer source="macos-vision" screenshot="/);
    assert.match(xml, /text="页面标题"/);
    assert.equal(fs.readFileSync(screenshotOutput, 'utf8'), 'png');
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('dumpCurrentPageXml falls back to synthetic OCR hierarchy when UI dump fails', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'page-xml-synthetic-'));
  const screenshotOutput = path.join(tmpDir, 'screen.png');
  const adb = {
    dumpUiXml: async () => {
      throw new Error('uiautomator unavailable');
    },
  };

  try {
    const xml = await dumpCurrentPageXml({
      adb,
      augmentOcr: true,
      screenshotOutput,
      captureScreenshotFn: async ({ outputPath }) => {
        fs.writeFileSync(outputPath, 'png');
      },
      recognizeScreenshotTextFn: async () => [
        { text: '兜底文本', confidence: 0.88, x1: 3, y1: 4, x2: 80, y2: 30 },
      ],
    });

    assert.match(xml, /source="synthetic-ocr"/);
    assert.match(xml, /dump-error="uiautomator unavailable"/);
    assert.match(xml, /text="兜底文本"/);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
