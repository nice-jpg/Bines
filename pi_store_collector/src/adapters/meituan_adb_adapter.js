const { AdbSession, tsId } = require('../automation/adb_session');
const { getMeituanEnv } = require('../apps/meituan/env');
const {
  collectMeituanStore,
  extractTextsFromUiXml,
  extractStoreFactsFromTexts,
  extractProductFactsFromTexts,
  extractReviewFactsFromTexts,
} = require('../apps/meituan/logic');

function createMeituanAdbAdapter(options = {}) {
  const env = getMeituanEnv(options);

  return {
    name: 'meituan-adb-adapter',
    async collect(task, context = {}) {
      const session = new AdbSession({
        deviceId: context.deviceId || '',
        runner: options.runner || null,
        artifactRoot: env.artifactRoot,
        appId: env.appId,
        taskId: task?.task_id || `task_${tsId()}`,
      });
      return collectMeituanStore({ task, session, env });
    },
  };
}

module.exports = {
  createMeituanAdbAdapter,
  extractTextsFromUiXml,
  extractStoreFactsFromTexts,
  extractProductFactsFromTexts,
  extractReviewFactsFromTexts,
};
