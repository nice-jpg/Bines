const fs = require('node:fs');
const path = require('node:path');

function loadEnvFile(envPath) {
  const resolved = path.resolve(envPath);
  if (!fs.existsSync(resolved)) return;
  const lines = fs.readFileSync(resolved, 'utf8').split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const idx = line.indexOf('=');
    if (idx <= 0) continue;
    const key = line.slice(0, idx).trim();
    let value = line.slice(idx + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = value;
  }
}

function toInt(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.floor(n) : fallback;
}

function getConfig() {
  loadEnvFile(process.env.ENV_FILE || path.resolve(__dirname, '..', 'config', 'example.env'));
  return {
    port: toInt(process.env.PI_SERVICE_PORT, 9080),
    dbPath: process.env.DB_PATH || path.resolve(__dirname, '..', 'data', 'pi_store_collector.db'),
    dataRoot: process.env.DATA_ROOT || path.resolve(__dirname, '..', 'data'),
    retryLimit: toInt(process.env.RETRY_LIMIT, 2),
  };
}

module.exports = {
  getConfig,
};
