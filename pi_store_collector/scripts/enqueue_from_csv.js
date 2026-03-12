#!/usr/bin/env node
const path = require('node:path');
const { getConfig } = require('../src/config');
const { openDb } = require('../src/db');
const { enqueueFromPoiCsv } = require('../src/task_service');

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

function main() {
  const args = parseArgs(process.argv);
  const config = getConfig();
  const csvPath = args['poi-csv-path'];
  if (!csvPath) {
    throw new Error('--poi-csv-path is required');
  }

  const platforms = (args.platforms || 'meituan,dianping,douyin')
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);

  const db = openDb(config.dbPath);
  const out = enqueueFromPoiCsv(db, path.resolve(csvPath), platforms);
  process.stdout.write(`${JSON.stringify({ ok: true, ...out })}\n`);
}

if (require.main === module) {
  try {
    main();
  } catch (err) {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  }
}
