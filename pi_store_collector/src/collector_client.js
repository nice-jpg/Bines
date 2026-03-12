class CollectorClient {
  constructor({ serverUrl, timeoutMs = 20000 }) {
    this.serverUrl = String(serverUrl || '').replace(/\/$/, '');
    this.timeoutMs = timeoutMs;
    if (!this.serverUrl) throw new Error('serverUrl is required');
  }

  async post(path, payload) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const resp = await fetch(`${this.serverUrl}${path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload || {}),
        signal: controller.signal,
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const body = await resp.json();
      if (body && body.error) throw new Error(body.error);
      return body;
    } finally {
      clearTimeout(timer);
    }
  }

  async get(path) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const resp = await fetch(`${this.serverUrl}${path}`, { signal: controller.signal });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const body = await resp.json();
      if (body && body.error) throw new Error(body.error);
      return body;
    } finally {
      clearTimeout(timer);
    }
  }

  async health() {
    return this.get('/health');
  }

  async queueStats() {
    return this.get('/queue_stats');
  }

  async qualityReport({ lookbackHours = 24 } = {}) {
    return this.get(`/quality_report?lookback_hours=${encodeURIComponent(String(lookbackHours))}`);
  }

  async manualReviewQueue({ status = 'PENDING', limit = 50 } = {}) {
    const qs = `status=${encodeURIComponent(status)}&limit=${encodeURIComponent(String(limit))}`;
    return this.get(`/manual_review_queue?${qs}`);
  }

  async enqueueFromCsv({ poiCsvPath, platforms }) {
    return this.post('/enqueue_from_csv', {
      poi_csv_path: poiCsvPath,
      platforms,
    });
  }

  async pullTask({ deviceId, platforms }) {
    const out = await this.post('/pull_task', {
      device_id: deviceId,
      platforms,
    });
    return out.task || null;
  }

  async pushResult(payload) {
    return this.post('/push_result', payload);
  }

  async heartbeat({ deviceId, accountId, platform, status, extra }) {
    return this.post('/heartbeat', {
      device_id: deviceId,
      account_id: accountId,
      platform,
      status,
      extra,
    });
  }
}

module.exports = {
  CollectorClient,
};
