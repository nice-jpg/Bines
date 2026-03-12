#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
POI_CSV_PATH="${1:-}"
PLATFORMS="${PLATFORMS:-meituan,dianping,douyin}"
LOOKBACK_HOURS="${LOOKBACK_HOURS:-24}"
EXPORT_DIR="${EXPORT_DIR:-$ROOT_DIR/data/exports/daily_$(date +%F)}"

if [[ -z "$POI_CSV_PATH" ]]; then
  echo "usage: $0 <poi_csv_path>"
  exit 1
fi

node "$ROOT_DIR/scripts/enqueue_from_csv.js" \
  --poi-csv-path "$POI_CSV_PATH" \
  --platforms "$PLATFORMS"

node "$ROOT_DIR/scripts/export_facts.js" \
  --out-dir "$EXPORT_DIR" \
  --lookback-hours "$LOOKBACK_HOURS"

echo "daily incremental workflow done: $EXPORT_DIR"
