#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');
const { runAnalysis } = require('./analysis_core');

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
  if (!args[key]) {
    throw new Error(`Missing required argument: --${key}`);
  }
  return args[key];
}

function loadScopeFromPoiDir(poiPath) {
  const scopePath = path.join(path.dirname(path.resolve(poiPath)), 'analysis_scope.json');
  if (!fs.existsSync(scopePath)) return null;
  return JSON.parse(fs.readFileSync(scopePath, 'utf8'));
}

function main() {
  const args = parseArgs(process.argv);
  const poiPath = required(args, 'poi');
  const scope = loadScopeFromPoiDir(poiPath);
  const centerLat = args['center-lat'] != null ? Number(args['center-lat']) : Number(scope?.center_lat);
  const centerLng = args['center-lng'] != null ? Number(args['center-lng']) : Number(scope?.center_lng);
  const radiusKm = args['radius-km'] != null ? Number(args['radius-km']) : Number(scope?.radius_km);
  if (!Number.isFinite(centerLat) || !Number.isFinite(centerLng) || !Number.isFinite(radiusKm)) {
    throw new Error(
      'Missing analysis scope. Run data_acquisition first (to generate analysis_scope.json), or pass --center-lat --center-lng --radius-km.',
    );
  }

  const result = runAnalysis({
    poiPath,
    contextPath: required(args, 'context'),
    dictionaryPath: required(args, 'dictionary'),
    outputDir: required(args, 'output'),
    centerLat,
    centerLng,
    radiusKm,
  });

  console.log('Analysis completed.');
  console.log(`Coverage ratio: ${(result.coverage * 100).toFixed(1)}%`);
  console.log('Outputs:');
  console.log(`- ${result.outputs.industry_scorecard}`);
  console.log(`- ${result.outputs.top_opportunities}`);
  console.log(`- ${result.outputs.analysis_report}`);
}

if (require.main === module) {
  try {
    main();
  } catch (err) {
    console.error(err.message || String(err));
    process.exit(1);
  }
}
