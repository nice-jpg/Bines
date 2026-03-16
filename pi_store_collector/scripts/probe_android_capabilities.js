#!/usr/bin/env node
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { AdbClient } = require('../src/device/adb_client');

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

async function runStep(name, fn) {
  try {
    const detail = await fn();
    return { name, ok: true, detail };
  } catch (err) {
    return { name, ok: false, detail: err.message || String(err) };
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const deviceId = args['device-id'] || process.env.DEVICE_ID;
  if (!deviceId) throw new Error('--device-id is required');

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'android-probe-'));
  const adb = new AdbClient({ serial: deviceId });

  const results = [];
  results.push(await runStep('device_model', async () => {
    const { stdout } = await adb.shell(['getprop', 'ro.product.model']);
    return stdout.trim();
  }));
  results.push(await runStep('clipboard_set_text', async () => {
    await adb.shellCommand('cmd clipboard set text "probe_text"');
    return 'ok';
  }));
  results.push(await runStep('paste_keyevent', async () => {
    await adb.keyevent(279);
    return 'ok';
  }));
  results.push(await runStep('uiautomator_dump', async () => {
    const xml = await adb.dumpUiXml('/sdcard/pi_store_collector_probe.xml');
    return xml.includes('<hierarchy') ? 'xml_ok' : 'no_xml';
  }));
  results.push(await runStep('screencap', async () => {
    const png = await adb.screenshotPng();
    if (!png) throw new Error('empty screenshot');
    const filePath = path.join(tmpDir, 'probe_screen.png');
    fs.writeFileSync(filePath, png, 'binary');
    return filePath;
  }));

  process.stdout.write(`${JSON.stringify({
    ok: results.every((x) => x.ok),
    device_id: deviceId,
    results,
  }, null, 2)}\n`);
}

main().catch((err) => {
  process.stderr.write(`${err.message || String(err)}\n`);
  process.exit(1);
});
