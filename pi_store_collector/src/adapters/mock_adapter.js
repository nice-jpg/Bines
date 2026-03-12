function createMockAdapter(platform) {
  return {
    name: `${platform}-mock-adapter`,
    async collect(task) {
      const now = new Date().toISOString();
      const baseName = task?.store_name || 'unknown_store';
      return {
        store_facts: {
          platform_store_id: `${platform}_${task?.task_id || 'unknown'}`,
          store_name: baseName,
          lat: task?.lat ?? null,
          lng: task?.lng ?? null,
          rating: 4.3,
          review_count: 120,
          monthly_orders: 260,
          avg_price: 42,
          crawl_time: now,
          confidence: 0.9,
        },
        product_facts: Array.from({ length: Math.min(task?.product_limit || 20, 3) }, (_, i) => ({
          product_id: `p_${i + 1}`,
          product_name: `${baseName}_product_${i + 1}`,
          price: 20 + i * 5,
          sales_text: `月售${100 - i * 10}`,
          rank_no: i + 1,
          crawl_time: now,
        })),
        review_facts: Array.from({ length: Math.min(task?.review_limit || 30, 3) }, (_, i) => ({
          review_id: `r_${i + 1}`,
          rating: 5 - i * 0.5,
          content: `${baseName} review ${i + 1}`,
          like_count: 10 - i,
          comment_time: now,
          crawl_time: now,
        })),
        raw_artifact_path: '',
      };
    },
  };
}

module.exports = {
  createMockAdapter,
};
