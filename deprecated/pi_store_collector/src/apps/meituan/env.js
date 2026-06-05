const path = require('node:path');

function getMeituanEnv(options = {}) {
  return {
    appId: 'meituan',
    packageName: options.packageName || 'com.sankuai.meituan',
    artifactRoot: options.artifactRoot || path.resolve(__dirname, '..', '..', '..', 'data', 'artifacts'),
    searchTapX: Number(options.searchTapX || 540),
    searchTapY: Number(options.searchTapY || 180),
    enterKeyCode: Number(options.enterKeyCode || 66),
    launchWaitMs: Number(options.launchWaitMs || 1800),
    inputWaitMs: Number(options.inputWaitMs || 500),
    resultWaitMs: Number(options.resultWaitMs || 2000),
    searchQueries: ['搜索', 'Search'],
    preSearchDumpPath: '/sdcard/pi_store_collector_presearch_ui.xml',
    resultDumpPath: '/sdcard/pi_store_collector_meituan_ui.xml',
  };
}

module.exports = {
  getMeituanEnv,
};
