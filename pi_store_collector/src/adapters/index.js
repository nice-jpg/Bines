const { createStubAdapter } = require('./base_stub_adapter');
const { createMockAdapter } = require('./mock_adapter');
const { createMeituanAdbAdapter } = require('./meituan_adb_adapter');

function buildAdapters({ useMock = false, useMeituanAdb = true, artifactRoot = '', meituanRunner = null } = {}) {
  if (useMock) {
    return {
      meituan: createMockAdapter('meituan'),
      dianping: createMockAdapter('dianping'),
      douyin: createMockAdapter('douyin'),
    };
  }
  return {
    meituan: useMeituanAdb
      ? createMeituanAdbAdapter({ artifactRoot, runner: meituanRunner || null })
      : createStubAdapter('meituan'),
    dianping: createStubAdapter('dianping'),
    douyin: createStubAdapter('douyin'),
  };
}

module.exports = {
  buildAdapters,
};
