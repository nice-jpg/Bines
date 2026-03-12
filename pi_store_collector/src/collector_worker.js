#!/usr/bin/env node
const { CollectorClient } = require('./collector_client');
const { buildAdapters } = require('./adapters');

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    const val = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : 'true';
    args[key] = val;
  }
  return args;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function runSingleIteration({ client, adapters, deviceId, accountId, platforms }) {
  const task = await client.pullTask({ deviceId, platforms });
  if (!task) return { status: 'IDLE' };

  const adapter = adapters[task.platform];
  if (!adapter) {
    await client.pushResult({
      task_id: task.task_id,
      status: 'FAILED',
      fail_reason: `NO_ADAPTER:${task.platform}`,
      account_id: accountId,
      device_id: deviceId,
    });
    return { status: 'FAILED', task_id: task.task_id, reason: 'NO_ADAPTER' };
  }

  try {
    await client.heartbeat({ deviceId, accountId, platform: task.platform, status: 'WORKING', extra: { task_id: task.task_id } });
    const result = await adapter.collect(task, { deviceId, accountId });

    await client.pushResult({
      task_id: task.task_id,
      status: 'SUCCESS',
      account_id: accountId,
      device_id: deviceId,
      store_facts: result.store_facts || null,
      product_facts: result.product_facts || [],
      review_facts: result.review_facts || [],
      raw_artifact_path: result.raw_artifact_path || '',
    });

    await client.heartbeat({ deviceId, accountId, platform: task.platform, status: 'IDLE', extra: { last_task: task.task_id } });
    return { status: 'SUCCESS', task_id: task.task_id, platform: task.platform };
  } catch (err) {
    await client.pushResult({
      task_id: task.task_id,
      status: 'FAILED',
      fail_reason: err.message || String(err),
      account_id: accountId,
      device_id: deviceId,
    });
    await client.heartbeat({ deviceId, accountId, platform: task.platform, status: 'ERROR', extra: { last_error: err.message || String(err) } });
    return { status: 'FAILED', task_id: task.task_id, platform: task.platform, reason: err.message || String(err) };
  }
}

async function runWorker(opts) {
  const {
    serverUrl,
    deviceId,
    accountId,
    platforms,
    loop,
    intervalMs,
    useMock,
    useMeituanAdb,
    artifactRoot,
    meituanRunner,
  } = opts;

  const client = new CollectorClient({ serverUrl });
  const adapters = buildAdapters({
    useMock,
    useMeituanAdb,
    artifactRoot,
    meituanRunner,
  });

  if (!loop) {
    return runSingleIteration({ client, adapters, deviceId, accountId, platforms });
  }

  while (true) {
    const out = await runSingleIteration({ client, adapters, deviceId, accountId, platforms });
    process.stdout.write(`${JSON.stringify(out)}\n`);
    await sleep(intervalMs);
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const serverUrl = args['server-url'] || process.env.PI_SERVER_URL || 'http://127.0.0.1:9080';
  const deviceId = args['device-id'] || process.env.DEVICE_ID || 'collector-device-01';
  const accountId = args['account-id'] || process.env.ACCOUNT_ID || 'collector-account-01';
  const platforms = (args.platforms || process.env.PLATFORMS || 'meituan,dianping,douyin')
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
  const loop = String(args.loop || process.env.WORKER_LOOP || 'true') === 'true';
  const intervalMs = Number(args['interval-ms'] || process.env.WORKER_INTERVAL_MS || 3000);
  const useMock = String(args['use-mock-adapter'] || process.env.USE_MOCK_ADAPTER || 'false') === 'true';
  const useMeituanAdb = String(args['use-meituan-adb'] || process.env.USE_MEITUAN_ADB || 'false') === 'true';
  const artifactRoot = args['artifact-root'] || process.env.COLLECTOR_ARTIFACT_ROOT || '';

  const out = await runWorker({
    serverUrl,
    deviceId,
    accountId,
    platforms,
    loop,
    intervalMs,
    useMock,
    useMeituanAdb,
    artifactRoot,
  });

  if (!loop) process.stdout.write(`${JSON.stringify(out)}\n`);
}

if (require.main === module) {
  main().catch((err) => {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  });
}

module.exports = {
  runSingleIteration,
  runWorker,
};
