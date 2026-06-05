#!/usr/bin/env node
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createMeituanAdbAdapter } = require('../src/adapters/meituan_adb_adapter');

async function main() {
  const calls = [];
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'meituan-dryrun-'));
  const fakeXml = `<hierarchy>
    <node text="测试门店"/>
    <node text="4.7"/>
    <node text="299条评价"/>
    <node text="招牌羊肉汤"/>
    <node text="¥28"/>
    <node text="味道不错，分量足"/>
  </hierarchy>`;

  async function runner(bin, args) {
    calls.push({ bin, args });
    if (args.includes('uiautomator') && args.includes('dump')) return { stdout: 'ok', stderr: '' };
    if (args.includes('exec-out') && args.includes('cat')) return { stdout: fakeXml, stderr: '' };
    return { stdout: '', stderr: '' };
  }

  const adapter = createMeituanAdbAdapter({
    artifactRoot: tmpDir,
    runner,
    searchTapX: 540,
    searchTapY: 180,
    launchWaitMs: 1,
    inputWaitMs: 1,
    resultWaitMs: 1,
  });

  const out = await adapter.collect({
    task_id: 'dryrun_001',
    store_name: '测试门店',
    lat: 33.64,
    lng: 116.96,
    product_limit: 5,
    review_limit: 5,
  }, { deviceId: 'android-dryrun' });

  process.stdout.write(`${JSON.stringify({
    ok: true,
    artifact_path: out.raw_artifact_path,
    store_facts: out.store_facts,
    product_count: out.product_facts.length,
    review_count: out.review_facts.length,
    adb_call_count: calls.length,
    temp_dir: tmpDir,
  }, null, 2)}\n`);
}

main().catch((err) => {
  process.stderr.write(`${err.message || String(err)}\n`);
  process.exit(1);
});
