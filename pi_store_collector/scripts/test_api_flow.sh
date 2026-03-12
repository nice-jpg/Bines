#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

TMP_DIR="$(mktemp -d)"
trap 'kill ${SERVER_PID:-0} >/dev/null 2>&1 || true; rm -rf "$TMP_DIR"' EXIT

cat > "$TMP_DIR/poi.csv" <<CSV
name,category_l2,lat,lng
小袁羊肉馆,中式快餐,33.6496,116.9641
CSV

export PI_SERVICE_PORT=19080
export DB_PATH="$TMP_DIR/pi_store_collector.db"

node src/server.js > "$TMP_DIR/server.log" 2>&1 &
SERVER_PID=$!

for _ in {1..30}; do
  if curl -sS "http://127.0.0.1:${PI_SERVICE_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.3
done

if ! curl -sS "http://127.0.0.1:${PI_SERVICE_PORT}/health" >/dev/null 2>&1; then
  echo "server failed to start" >&2
  cat "$TMP_DIR/server.log" >&2 || true
  exit 1
fi

echo "[api] health"
curl -sS "http://127.0.0.1:${PI_SERVICE_PORT}/health"

echo "[api] enqueue"
curl -sS -X POST "http://127.0.0.1:${PI_SERVICE_PORT}/enqueue_from_csv" \
  -H 'content-type: application/json' \
  -d "{\"poi_csv_path\":\"$TMP_DIR/poi.csv\",\"platforms\":[\"meituan\"]}"

echo "[api] pull"
TASK_JSON=$(curl -sS -X POST "http://127.0.0.1:${PI_SERVICE_PORT}/pull_task" \
  -H 'content-type: application/json' \
  -d '{"device_id":"android-01","platforms":["meituan"]}')
echo "$TASK_JSON"
TASK_ID=$(echo "$TASK_JSON" | sed -n 's/.*"task_id":"\([^"]*\)".*/\1/p')

if [[ -z "$TASK_ID" ]]; then
  echo "task_id missing" >&2
  exit 1
fi

echo "[api] push result"
curl -sS -X POST "http://127.0.0.1:${PI_SERVICE_PORT}/push_result" \
  -H 'content-type: application/json' \
  -d "{\"task_id\":\"$TASK_ID\",\"status\":\"SUCCESS\",\"account_id\":\"acc-01\",\"device_id\":\"android-01\",\"store_facts\":{\"store_name\":\"小袁羊肉馆\",\"rating\":4.4,\"review_count\":120,\"monthly_orders\":260,\"avg_price\":42},\"product_facts\":[{\"product_name\":\"羊肉汤\",\"price\":26}],\"review_facts\":[{\"rating\":5,\"content\":\"好吃\"}]}"

echo "[api] queue stats"
curl -sS "http://127.0.0.1:${PI_SERVICE_PORT}/queue_stats"

echo "[api] quality report"
curl -sS "http://127.0.0.1:${PI_SERVICE_PORT}/quality_report?lookback_hours=24"

echo "[api] manual review queue"
curl -sS "http://127.0.0.1:${PI_SERVICE_PORT}/manual_review_queue?status=PENDING&limit=10"

echo "[ok] api flow finished"
