const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

const execFileAsync = promisify(execFile);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function escapeForAdbInputText(text) {
  return String(text || '')
    .replace(/\r?\n+/g, ' ')
    .replace(/\s+/g, '%s')
    .replace(/([\\()&|><;$`"'!*?\[\]{}~])/g, '\\$1');
}

function isAsciiText(text) {
  return /^[\x00-\x7F]*$/.test(String(text || ''));
}

function shellQuote(text) {
  return `'${String(text || '').replace(/'/g, `'\\''`)}'`;
}

function stripUiAutomatorNoise(text) {
  const raw = String(text || '');
  const start = raw.indexOf('<?xml');
  if (start >= 0) return raw.slice(start).trim();
  const hierarchyStart = raw.indexOf('<hierarchy');
  if (hierarchyStart >= 0) return raw.slice(hierarchyStart).trim();
  return raw.trim();
}

function escapeXmlAttr(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function convertDumpsysToXml(dumpsysOutput) {
  const lines = String(dumpsysOutput || '').split('\n');
  const nodes = [];
  let inHierarchy = false;

  for (const line of lines) {
    if (line.includes('View Hierarchy:')) {
      inHierarchy = true;
      continue;
    }
    if (!inHierarchy) continue;

    if (!line.trim() || (line.trim() && !line.startsWith(' '))) {
      if (nodes.length) break;
      continue;
    }

    const stripped = line.trim();
    if (!stripped) continue;

    const classMatch = stripped.match(/^([\w.$]+)\{/);
    if (!classMatch) continue;
    const className = classMatch[1];

    const coordMatch = stripped.match(/(\d+),(\d+)-(\d+),(\d+)/);
    if (!coordMatch) continue;
    const bounds = `[${coordMatch[1]},${coordMatch[2]}][${coordMatch[3]},${coordMatch[4]}]`;

    const textMatch = stripped.match(/text="([^"]*)"/) || stripped.match(/text=([^\s}]+)/);
    const descMatch = stripped.match(/desc="([^"]*)"/);
    const ridMatch = stripped.match(/id\/([^\s}]+)/);

    const text = textMatch ? textMatch[1] : '';
    const contentDesc = descMatch ? descMatch[1] : '';
    const resourceId = ridMatch ? ridMatch[1] : '';

    if (!text && !contentDesc) continue;

    nodes.push(
      `<node class="${escapeXmlAttr(className)}" text="${escapeXmlAttr(text)}" ` +
      `content-desc="${escapeXmlAttr(contentDesc)}" resource-id="${escapeXmlAttr(resourceId)}" ` +
      `bounds="${bounds}" clickable="true" />`,
    );
  }

  if (!nodes.length) return null;
  return `<?xml version="1.0" encoding="UTF-8"?>\n<hierarchy rotation="0">\n  ${nodes.join('\n  ')}\n</hierarchy>`;
}

function parseBounds(boundsText) {
  const match = String(boundsText || '').match(/\[(\d+),(\d+)\]\[(\d+),(\d+)\]/);
  if (!match) return null;
  return {
    x1: Number(match[1]),
    y1: Number(match[2]),
    x2: Number(match[3]),
    y2: Number(match[4]),
  };
}

function getCenterFromBounds(boundsText) {
  const bounds = parseBounds(boundsText);
  if (!bounds) return null;
  return {
    x: Math.floor((bounds.x1 + bounds.x2) / 2),
    y: Math.floor((bounds.y1 + bounds.y2) / 2),
  };
}

function findElementsByText(uiXml, query, { exact = false } = {}) {
  const q = String(query || '');
  const out = [];
  const re = /<node\b([^>]+?)\/>/g;
  let m;
  while ((m = re.exec(String(uiXml || ''))) !== null) {
    const attrs = m[1];
    const textMatch = attrs.match(/\btext="([^"]*)"/);
    const descMatch = attrs.match(/\bcontent-desc="([^"]*)"/);
    const boundsMatch = attrs.match(/\bbounds="([^"]*)"/);
    const ridMatch = attrs.match(/\bresource-id="([^"]*)"/);
    const classMatch = attrs.match(/\bclass="([^"]*)"/);
    const clickableMatch = attrs.match(/\bclickable="([^"]*)"/);
    const text = textMatch ? textMatch[1] : '';
    const contentDesc = descMatch ? descMatch[1] : '';
    const haystack = [text, contentDesc];
    const matched = exact
      ? haystack.some((value) => value === q)
      : haystack.some((value) => value.includes(q));
    if (!matched || !boundsMatch) continue;
    const center = getCenterFromBounds(boundsMatch[1]);
    if (!center) continue;
    out.push({
      text,
      content_desc: contentDesc,
      resource_id: ridMatch ? ridMatch[1] : '',
      class: classMatch ? classMatch[1] : '',
      bounds: boundsMatch[1],
      center_x: center.x,
      center_y: center.y,
      clickable: clickableMatch ? clickableMatch[1] === 'true' : false,
    });
  }
  return out;
}

class AdbClient {
  constructor({ serial = '', runner = null } = {}) {
    this.serial = serial || '';
    this.runner = runner || this.defaultRunner.bind(this);
  }

  async defaultRunner(bin, args) {
    const { stdout, stderr } = await execFileAsync(bin, args, { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
    return { stdout, stderr };
  }

  adbArgs(args) {
    if (this.serial) return ['-s', this.serial, ...args];
    return args;
  }

  async runAdb(args) {
    return this.runner('adb', this.adbArgs(args));
  }

  async shell(cmdArgs) {
    return this.runAdb(['shell', ...cmdArgs]);
  }

  async shellCommand(command) {
    return this.runAdb(['shell', String(command)]);
  }

  async launchApp(packageName) {
    await this.shell(['monkey', '-p', packageName, '-c', 'android.intent.category.LAUNCHER', '1']);
  }

  async prepareDevice() {
    await this.shell(['device_config', 'set_sync_disabled_for_tests', 'persistent']).catch(() => {});
    await this.shell(['device_config', 'put', 'activity_manager', 'max_phantom_processes', '2147483647']).catch(() => {});
    await this.shell(['settings', 'put', 'global', 'settings_enable_monitor_phantom_procs', 'false']).catch(() => {});
    await this.shell(['rm', '-f', '/sdcard/window_dump.xml']).catch(() => {});
  }

  async tap(x, y) {
    await this.shell(['input', 'tap', String(Math.round(Number(x))), String(Math.round(Number(y)))]);
  }

  async inputText(text) {
    const cleaned = escapeForAdbInputText(text);
    await this.shell(['input', 'text', cleaned]);
  }

  async push(localPath, devicePath) {
    await this.runAdb(['push', localPath, devicePath]);
  }

  async pasteText(text, devicePath = '/data/local/tmp/pi_store_collector_input.txt') {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-store-collector-input-'));
    const localPath = path.join(tmpDir, 'input.txt');
    fs.writeFileSync(localPath, String(text || ''), 'utf8');
    try {
      await this.push(localPath, devicePath);
      await this.shellCommand(`cmd clipboard set text "$(cat ${shellQuote(devicePath)})"`);
      await this.keyevent(279);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  }

  async enterText(text) {
    if (isAsciiText(text)) {
      await this.inputText(text);
      return;
    }
    await this.pasteText(text);
  }

  async keyevent(code) {
    await this.shell(['input', 'keyevent', String(code)]);
  }

  async dumpUiXml(devicePath = '/sdcard/pi_store_collector_ui.xml') {
    const errors = [];

    try {
      const { stdout, stderr } = await this.runAdb(['exec-out', 'uiautomator', 'dump', '/dev/tty']);
      const xml = stripUiAutomatorNoise(stdout || stderr || '');
      if (xml.includes('<hierarchy')) return xml;
      errors.push(`exec-out:/dev/tty returned no xml`);
    } catch (err) {
      errors.push(`exec-out:/dev/tty ${err.message || String(err)}`);
    }

    try {
      const { stdout, stderr } = await this.shell(['uiautomator', 'dump', '--compressed', devicePath]);
      const maybeOutput = stripUiAutomatorNoise(stdout || stderr || '');
      if (maybeOutput.includes('<hierarchy')) return maybeOutput;
      const { stdout: pulled } = await this.runAdb(['exec-out', 'cat', devicePath]);
      const xml = stripUiAutomatorNoise(pulled || '');
      if (xml.includes('<hierarchy')) return xml;
      errors.push(`compressed:${devicePath} returned no xml`);
    } catch (err) {
      errors.push(`compressed:${devicePath} ${err.message || String(err)}`);
    }

    const candidates = [devicePath, '/data/local/tmp/pi_store_collector_ui.xml'];
    for (const candidate of candidates) {
      try {
        await this.shell(['uiautomator', 'dump', candidate]);
        const { stdout } = await this.runAdb(['exec-out', 'cat', candidate]);
        const xml = stripUiAutomatorNoise(stdout || '');
        if (xml.includes('<hierarchy')) return xml;
        errors.push(`file:${candidate} returned no xml`);
      } catch (err) {
        errors.push(`file:${candidate} ${err.message || String(err)}`);
      }
    }

    try {
      const { stdout, stderr } = await this.shell(['dumpsys', 'activity', 'top', '-a']);
      const xml = convertDumpsysToXml(stdout || stderr || '');
      if (xml && xml.includes('<hierarchy')) return xml;
      errors.push('dumpsys:activity top returned no convertible hierarchy');
    } catch (err) {
      errors.push(`dumpsys:activity top ${err.message || String(err)}`);
    }

    throw new Error(`UI_DUMP_FAILED: ${errors.join(' | ')}`);
  }

  async screenshotPng() {
    const { stdout } = await this.runAdb(['exec-out', 'screencap', '-p']);
    return stdout || '';
  }

  async wait(ms) {
    await sleep(ms);
  }
}

module.exports = {
  AdbClient,
  sleep,
  escapeForAdbInputText,
  isAsciiText,
  shellQuote,
  stripUiAutomatorNoise,
  convertDumpsysToXml,
  parseBounds,
  getCenterFromBounds,
  findElementsByText,
};
