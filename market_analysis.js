#!/usr/bin/env node
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

function main() {
  const args = parseArgs(process.argv);
  const result = runAnalysis({
    poiPath: required(args, 'poi'),
    contextPath: required(args, 'context'),
    dictionaryPath: required(args, 'dictionary'),
    outputDir: required(args, 'output'),
    centerLat: Number(required(args, 'center-lat')),
    centerLng: Number(required(args, 'center-lng')),
    radiusKm: Number(args['radius-km'] || 1.5),
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
