const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

const execFileAsync = promisify(execFile);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

  async launchApp(packageName) {
    await this.shell(['monkey', '-p', packageName, '-c', 'android.intent.category.LAUNCHER', '1']);
  }

  async tap(x, y) {
    await this.shell(['input', 'tap', String(Math.round(Number(x))), String(Math.round(Number(y)))]);
  }

  async inputText(text) {
    const cleaned = String(text || '')
      .replace(/\s+/g, '%s')
      .replace(/[&|><;$`"']/g, '');
    await this.shell(['input', 'text', cleaned]);
  }

  async keyevent(code) {
    await this.shell(['input', 'keyevent', String(code)]);
  }

  async dumpUiXml(devicePath = '/sdcard/pi_store_collector_ui.xml') {
    await this.shell(['uiautomator', 'dump', devicePath]);
    const { stdout } = await this.runAdb(['exec-out', 'cat', devicePath]);
    return stdout || '';
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
};
