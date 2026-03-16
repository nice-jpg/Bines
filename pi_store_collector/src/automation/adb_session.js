const fs = require('node:fs');
const path = require('node:path');
const { AdbClient, findElementsByText } = require('../device/adb_client');

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function tsId() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

class AdbSession {
  constructor({ deviceId = '', runner = null, artifactRoot, appId, taskId }) {
    this.adb = new AdbClient({ serial: deviceId, runner });
    this.runDir = path.join(artifactRoot, appId, taskId);
    ensureDir(this.runDir);
  }

  async prepareAndLaunch(env) {
    await this.adb.prepareDevice();
    await this.adb.launchApp(env.packageName);
    await this.adb.wait(env.launchWaitMs);
  }

  async tapByTextOrFallback({ queries, dumpPath, fallbackX, fallbackY }) {
    try {
      const uiXml = await this.adb.dumpUiXml(dumpPath);
      for (const query of queries) {
        const items = findElementsByText(uiXml, query, { exact: false });
        const first = items.find((item) => item.center_x >= 0 && item.center_y >= 0);
        if (first) {
          await this.adb.tap(first.center_x, first.center_y);
          return { mode: 'selector', uiXml, target: first };
        }
      }
    } catch {
      // fall back below
    }
    await this.adb.tap(fallbackX, fallbackY);
    return { mode: 'coordinates', uiXml: '', target: null };
  }

  async enterSearchText(text, waitMs, enterKeyCode) {
    await this.adb.wait(waitMs);
    await this.adb.enterText(text);
    await this.adb.keyevent(enterKeyCode);
  }

  async dumpUiWithArtifact(label, devicePath) {
    const uiXml = await this.adb.dumpUiXml(devicePath);
    const uiPath = path.join(this.runDir, `${label}_${tsId()}.xml`);
    fs.writeFileSync(uiPath, uiXml, 'utf8');
    return { uiXml, uiPath };
  }

  async captureFailureArtifact() {
    const screenshotPath = path.join(this.runDir, `failure_screen_${tsId()}.png`);
    try {
      const png = await this.adb.screenshotPng();
      if (!png) return '';
      fs.writeFileSync(screenshotPath, png, 'binary');
      return screenshotPath;
    } catch {
      return '';
    }
  }
}

module.exports = {
  AdbSession,
  tsId,
};
