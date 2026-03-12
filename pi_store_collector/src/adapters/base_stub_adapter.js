function createStubAdapter(platform) {
  return {
    name: `${platform}-stub-adapter`,
    async collect(task, context = {}) {
      const err = new Error(`NOT_IMPLEMENTED_ADAPTER:${platform}`);
      err.code = 'NOT_IMPLEMENTED_ADAPTER';
      err.platform = platform;
      err.task_id = task?.task_id;
      err.context = context;
      throw err;
    },
  };
}

module.exports = {
  createStubAdapter,
};
