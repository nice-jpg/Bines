const test = require('node:test');
const assert = require('node:assert/strict');

const {
  AdbClient,
  escapeForAdbInputText,
  isAsciiText,
  shellQuote,
  stripUiAutomatorNoise,
  convertDumpsysToXml,
  findElementsByText,
} = require('../src/device/adb_client');

test('ascii detection and escaping behave as expected', () => {
  assert.equal(isAsciiText('abc(1)'), true);
  assert.equal(isAsciiText('三陶自习室'), false);
  assert.equal(escapeForAdbInputText('A (B)'), 'A%s\\(B\\)');
  assert.equal(shellQuote('/data/local/tmp/a.txt'), "'/data/local/tmp/a.txt'");
  assert.equal(stripUiAutomatorNoise('UI hierchary dumped to: /dev/tty\n<?xml version="1.0"?><hierarchy/>'), '<?xml version="1.0"?><hierarchy/>');
});

test('convertDumpsysToXml extracts clickable nodes', () => {
  const dumpsys = `
Some Header
  View Hierarchy:
    android.widget.TextView{id/search V.ED.... 100,200-300,260 text="搜索"}
    android.widget.TextView{id/store V.ED.... 120,320-480,380 text="三陶自习室(一中校区)"}

Other Section
`;
  const xml = convertDumpsysToXml(dumpsys);
  assert.ok(xml);
  const found = findElementsByText(xml, '搜索');
  assert.equal(found.length, 1);
  assert.equal(found[0].center_x, 200);
  assert.equal(found[0].center_y, 230);
});

test('enterText uses input text for ascii', async () => {
  const calls = [];
  const adb = new AdbClient({
    serial: 'device-01',
    runner: async (bin, args) => {
      calls.push({ bin, args });
      return { stdout: '', stderr: '' };
    },
  });

  await adb.enterText('Store (A)');

  const inputTextCall = calls.find((x) => x.args.includes('input') && x.args.includes('text'));
  assert.ok(inputTextCall);
  assert.equal(calls.some((x) => x.args.includes('push')), false);
});

test('enterText uses clipboard paste for non-ascii', async () => {
  const calls = [];
  const adb = new AdbClient({
    serial: 'device-02',
    runner: async (bin, args) => {
      calls.push({ bin, args });
      return { stdout: '', stderr: '' };
    },
  });

  await adb.enterText('三陶自习室(一中校区)');

  const pushCall = calls.find((x) => x.args.includes('push'));
  assert.ok(pushCall);
  const clipboardCall = calls.find((x) => x.args.length === 4 && String(x.args[3]).includes('cmd clipboard set text'));
  assert.ok(clipboardCall);
  assert.ok(String(clipboardCall.args[3]).includes("cat '/data/local/tmp/pi_store_collector_input.txt'"));
  const pasteKeyCall = calls.find((x) => x.args.includes('keyevent') && x.args.includes('279'));
  assert.ok(pasteKeyCall);
  assert.equal(calls.some((x) => x.args.includes('input') && x.args.includes('text')), false);
});

test('dumpUiXml falls back from exec-out to tmp file', async () => {
  const calls = [];
  const adb = new AdbClient({
    serial: 'device-03',
    runner: async (bin, args) => {
      calls.push({ bin, args });
      if (args[2] === 'uiautomator' && args[3] === 'dump' && args[4] === '/dev/tty') {
        throw new Error('exec-out dump failed');
      }
      if (args.includes('uiautomator') && args.includes('dump') && args.includes('/data/local/tmp/pi_store_collector_ui.xml')) {
        return { stdout: 'UI hierchary dumped', stderr: '' };
      }
      if (args.includes('exec-out') && args.includes('cat') && args.includes('/data/local/tmp/pi_store_collector_ui.xml')) {
        return { stdout: 'UI hierchary dumped to: /data/local/tmp/pi_store_collector_ui.xml\n<?xml version="1.0"?><hierarchy />', stderr: '' };
      }
      if (args.includes('uiautomator') && args.includes('dump')) {
        throw new Error('primary path failed');
      }
      throw new Error(`unexpected call: ${args.join(' ')}`);
    },
  });

  const xml = await adb.dumpUiXml('/sdcard/pi_store_collector_ui.xml');
  assert.ok(xml.includes('<hierarchy'));
  assert.ok(calls.some((x) => x.args.includes('/dev/tty')));
  assert.ok(calls.some((x) => x.args.includes('/data/local/tmp/pi_store_collector_ui.xml')));
});

test('dumpUiXml falls back to dumpsys conversion', async () => {
  const adb = new AdbClient({
    serial: 'device-04',
    runner: async (bin, args) => {
      if (args.includes('dumpsys')) {
        return {
          stdout: 'View Hierarchy:\n  android.widget.TextView{id/search V.ED.... 100,200-300,260 text="搜索"}\n',
          stderr: '',
        };
      }
      throw new Error('forced failure');
    },
  });

  const xml = await adb.dumpUiXml('/sdcard/pi_store_collector_ui.xml');
  assert.ok(xml.includes('<hierarchy'));
  assert.ok(xml.includes('搜索'));
});
