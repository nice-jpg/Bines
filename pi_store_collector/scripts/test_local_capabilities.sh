#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "[test] syntax check"
node --check src/server.js
node --check src/task_service.js
node --check src/db.js
node --check src/report_service.js
node --check src/collector_client.js
node --check src/collector_worker.js
node --check scripts/enqueue_from_csv.js
node --check scripts/export_facts.js
node --check scripts/test_meituan_adapter_dryrun.js

echo "[test] unit tests"
node --test tests/*.test.js

echo "[test] meituan adapter dryrun"
node scripts/test_meituan_adapter_dryrun.js >/dev/null

echo "[ok] local capability tests passed"
