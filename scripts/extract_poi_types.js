#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');

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

function required(args, key) {
  if (!args[key]) throw new Error(`Missing required arg --${key}`);
  return args[key];
}

function main() {
  const args = parseArgs(process.argv);
  const input = path.resolve(required(args, 'input'));
  const output = path.resolve(args.output || '');
  const mode = (args.mode || 'unique').toLowerCase();

  const payload = JSON.parse(fs.readFileSync(input, 'utf8'));
  const pois = Array.isArray(payload.pois) ? payload.pois : [];

  const types = pois
    .map((p) => String(p.type || '').trim())
    .filter(Boolean);

  const result = mode === 'all' ? types : [...new Set(types)].sort((a, b) => a.localeCompare(b, 'zh-CN'));

  if (output) {
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, `${result.join('\n')}\n`, 'utf8');
    process.stdout.write(JSON.stringify({ input, output, count: result.length, mode }) + '\n');
  } else {
    process.stdout.write(`${result.join('\n')}\n`);
  }
}

if (require.main === module) {
  try {
    main();
  } catch (err) {
    process.stderr.write(`${err.message || String(err)}\n`);
    process.exit(1);
  }
}
