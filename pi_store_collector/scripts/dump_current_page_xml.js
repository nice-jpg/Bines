#!/usr/bin/env node
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { AdbClient } = require('../src/device/adb_client');

const execFileAsync = promisify(execFile);

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : 'true';
    args[key] = value;
  }
  return args;
}

function adbArgs(serial, args) {
  return serial ? ['-s', serial, ...args] : args;
}

function escapeXmlAttr(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function captureScreenshot({ deviceId = '', outputPath }) {
  const { stdout } = await execFileAsync('adb', adbArgs(deviceId, ['exec-out', 'screencap', '-p']), {
    encoding: 'buffer',
    maxBuffer: 20 * 1024 * 1024,
  });
  fs.writeFileSync(outputPath, stdout);
  return outputPath;
}

async function recognizeScreenshotText({ imagePath, ocrScriptPath = path.join(__dirname, 'ocr_image.swift') }) {
  const { stdout } = await execFileAsync('/usr/bin/swift', [ocrScriptPath, imagePath], {
    encoding: 'utf8',
    maxBuffer: 10 * 1024 * 1024,
  });
  return JSON.parse(stdout || '[]');
}

function buildOcrLayer(nodes, { screenshotPath = '' } = {}) {
  const attrs = [
    'source="macos-vision"',
    screenshotPath ? `screenshot="${escapeXmlAttr(screenshotPath)}"` : '',
  ].filter(Boolean).join(' ');
  const body = nodes.map((node, index) => {
    const bounds = `[${node.x1},${node.y1}][${node.x2},${node.y2}]`;
    const confidence = Number.isFinite(node.confidence) ? node.confidence.toFixed(4) : '';
    return `<ocr-node index="${index}" text="${escapeXmlAttr(node.text)}" confidence="${confidence}" bounds="${bounds}" />`;
  }).join('');
  return `<ocr-layer ${attrs}>${body}</ocr-layer>`;
}

function appendLayerToHierarchy(xml, layerXml) {
  const trimmed = String(xml || '').trim();
  if (!layerXml) return trimmed;
  const closing = '</hierarchy>';
  const closingIndex = trimmed.lastIndexOf(closing);
  if (closingIndex >= 0) {
    return `${trimmed.slice(0, closingIndex)}${layerXml}${trimmed.slice(closingIndex)}`;
  }
  const selfClosing = trimmed.match(/<hierarchy\b([^>]*)\/>\s*$/);
  if (selfClosing) {
    return trimmed.replace(/<hierarchy\b([^>]*)\/>\s*$/, `<hierarchy$1>${layerXml}</hierarchy>`);
  }
  throw new Error('UI_DUMP_FAILED: cannot append OCR layer to hierarchy XML');
}

function buildSyntheticHierarchy({ reason = '' } = {}) {
  const reasonAttr = reason ? ` dump-error="${escapeXmlAttr(reason)}"` : '';
  return `<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0" source="synthetic-ocr"${reasonAttr}></hierarchy>`;
}

async function dumpCurrentPageXml({
  deviceId = '',
  output = '',
  deviceDumpPath = '/sdcard/pi_store_collector_current_page.xml',
  augmentOcr = false,
  screenshotOutput = '',
  probe = false,
  adb = null,
  ocrScriptPath = path.join(__dirname, 'ocr_image.swift'),
  captureScreenshotFn = captureScreenshot,
  recognizeScreenshotTextFn = recognizeScreenshotText,
} = {}) {
  const client = adb || new AdbClient({ serial: deviceId });
  let xml = '';
  try {
    if (probe && client.probeUiDumpStrategy) {
      const detected = await client.probeUiDumpStrategy(deviceDumpPath);
      xml = detected.xml;
    }
    xml = await client.dumpUiXml(deviceDumpPath);
    if (!xml.includes('<hierarchy')) {
      throw new Error('UI_DUMP_FAILED: output does not contain <hierarchy');
    }
  } catch (err) {
    if (!augmentOcr) throw err;
    xml = buildSyntheticHierarchy({ reason: err.message || String(err) });
  }

  if (augmentOcr) {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'page-xml-ocr-'));
    const screenshotPath = screenshotOutput || path.join(tmpDir, 'screen.png');
    try {
      await captureScreenshotFn({ deviceId, outputPath: screenshotPath });
      const ocrNodes = await recognizeScreenshotTextFn({ imagePath: screenshotPath, ocrScriptPath });
      xml = appendLayerToHierarchy(xml, buildOcrLayer(ocrNodes, { screenshotPath }));
    } finally {
      if (!screenshotOutput) fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  }

  if (output) {
    fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true });
    fs.writeFileSync(output, `${xml.trim()}\n`, 'utf8');
  }

  return xml.trim();
}

async function main() {
  const args = parseArgs(process.argv);
  const xml = await dumpCurrentPageXml({
    deviceId: args['device-id'] || process.env.DEVICE_ID || '',
    output: args.output || '',
    deviceDumpPath: args['device-dump-path'] || '/sdcard/pi_store_collector_current_page.xml',
    augmentOcr: args['augment-ocr'] === 'true',
    screenshotOutput: args['screenshot-output'] || '',
    probe: args.probe === 'true',
  });

  if (!args.output) {
    process.stdout.write(`${xml}\n`);
  } else if (args.verbose === 'true') {
    process.stderr.write(`wrote ${args.output}\n`);
  }
}

if (require.main === module) {
  main().catch((err) => {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  });
}

module.exports = {
  appendLayerToHierarchy,
  buildOcrLayer,
  buildSyntheticHierarchy,
  captureScreenshot,
  dumpCurrentPageXml,
  parseArgs,
  recognizeScreenshotText,
};
