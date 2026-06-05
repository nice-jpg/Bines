#!/usr/bin/env node
const { spawn } = require('node:child_process');
const path = require('node:path');
const { CollectorClient } = require('../src/collector_client');

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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHealth(client, retries = 40, intervalMs = 250) {
  let lastErr = null;
  for (let i = 0; i < retries; i += 1) {
    try {
      const out = await client.health();
      if (out && out.ok) return out;
    } catch (err) {
      lastErr = err;
    }
    await sleep(intervalMs);
  }
  throw lastErr || new Error('server health check timed out');
}

function spawnNode(scriptPath, args, extraEnv = {}) {
  return spawn(process.execPath, [scriptPath, ...args], {
    cwd: path.resolve(__dirname, '..'),
    env: { ...process.env, ...extraEnv },
    stdio: 'inherit',
  });
}

async function main() {
  const args = parseArgs(process.argv);
  const deviceId = args['device-id'] || process.env.DEVICE_ID;
  if (!deviceId) {
    throw new Error('--device-id is required');
  }

  const port = String(args.port || process.env.PI_SERVICE_PORT || '9080');
  const serverUrl = `http://127.0.0.1:${port}`;
  const poiCsvPath = args['poi-csv-path'] || '';
  const platforms = (args.platforms || process.env.PLATFORMS || 'meituan')
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
  const artifactRoot = args['artifact-root'] || process.env.COLLECTOR_ARTIFACT_ROOT || '';
  const dbPath = args['db-path'] || process.env.DB_PATH || '';

  const serverEnv = { PI_SERVICE_PORT: port };
  if (dbPath) serverEnv.DB_PATH = dbPath;

  const serverProc = spawnNode(path.resolve(__dirname, '..', 'src', 'server.js'), [], serverEnv);
  const shutdown = () => {
    if (!serverProc.killed) serverProc.kill('SIGTERM');
  };
  process.on('exit', shutdown);
  process.on('SIGINT', () => {
    shutdown();
    process.exit(130);
  });
  process.on('SIGTERM', () => {
    shutdown();
    process.exit(143);
  });

  const client = new CollectorClient({ serverUrl, timeoutMs: 3000 });
  await waitForHealth(client);

  if (poiCsvPath) {
    await client.enqueueFromCsv({ poiCsvPath: path.resolve(poiCsvPath), platforms });
  }

  const workerArgs = [
    path.resolve(__dirname, '..', 'src', 'collector_worker.js'),
    '--server-url', serverUrl,
    '--device-id', deviceId,
    '--platforms', platforms.join(','),
    '--loop', 'false',
  ];
  if (artifactRoot) {
    workerArgs.push('--artifact-root', artifactRoot);
  }

  const workerProc = spawn(process.execPath, workerArgs, {
    cwd: path.resolve(__dirname, '..'),
    env: { ...process.env },
    stdio: 'inherit',
  });

  const exitCode = await new Promise((resolve, reject) => {
    workerProc.on('error', reject);
    workerProc.on('exit', resolve);
  });

  shutdown();
  if (exitCode !== 0) {
    process.exit(Number(exitCode) || 1);
  }
}

if (require.main === module) {
  main().catch((err) => {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  });
}
