const fs = require('node:fs');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');

function nowIso() {
  return new Date().toISOString();
}

function ensureDir(filePath) {
  fs.mkdirSync(path.dirname(path.resolve(filePath)), { recursive: true });
}

function openDb(dbPath) {
  ensureDir(dbPath);
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA foreign_keys = ON;');
  db.exec(`
    CREATE TABLE IF NOT EXISTS tasks (
      task_id TEXT PRIMARY KEY,
      platform TEXT NOT NULL,
      store_name TEXT NOT NULL,
      lat REAL,
      lng REAL,
      review_limit INTEGER NOT NULL DEFAULT 30,
      product_limit INTEGER NOT NULL DEFAULT 20,
      status TEXT NOT NULL DEFAULT 'PENDING',
      priority INTEGER NOT NULL DEFAULT 0,
      scheduled_at TEXT NOT NULL,
      started_at TEXT,
      finished_at TEXT,
      device_id TEXT,
      account_id TEXT,
      retry_count INTEGER NOT NULL DEFAULT 0,
      fail_reason TEXT,
      payload_json TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_status_platform_sched
    ON tasks(status, platform, scheduled_at, priority);

    CREATE TABLE IF NOT EXISTS store_facts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      platform_store_id TEXT,
      store_name TEXT NOT NULL,
      lat REAL,
      lng REAL,
      rating REAL,
      review_count INTEGER,
      monthly_orders INTEGER,
      avg_price REAL,
      crawl_time TEXT NOT NULL,
      confidence REAL,
      raw_json TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS product_facts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      store_name TEXT NOT NULL,
      product_id TEXT,
      product_name TEXT NOT NULL,
      price REAL,
      sales_text TEXT,
      rank_no INTEGER,
      crawl_time TEXT NOT NULL,
      raw_json TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS review_facts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      store_name TEXT NOT NULL,
      review_id TEXT,
      rating REAL,
      content TEXT,
      like_count INTEGER,
      comment_time TEXT,
      crawl_time TEXT NOT NULL,
      raw_json TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS crawl_audit (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      account_id TEXT,
      device_id TEXT,
      status TEXT NOT NULL,
      fail_reason TEXT,
      retry_count INTEGER NOT NULL DEFAULT 0,
      raw_artifact_path TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS heartbeats (
      device_id TEXT NOT NULL,
      account_id TEXT,
      platform TEXT NOT NULL,
      status TEXT NOT NULL,
      extra_json TEXT,
      last_seen TEXT NOT NULL,
      PRIMARY KEY (device_id, platform)
    );

    CREATE TABLE IF NOT EXISTS manual_review_queue (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      task_id TEXT NOT NULL,
      platform TEXT NOT NULL,
      reason TEXT NOT NULL,
      payload_json TEXT,
      status TEXT NOT NULL DEFAULT 'PENDING',
      created_at TEXT NOT NULL
    );
  `);

  return db;
}

function withTransaction(db, fn) {
  db.exec('BEGIN;');
  try {
    const out = fn();
    db.exec('COMMIT;');
    return out;
  } catch (err) {
    db.exec('ROLLBACK;');
    throw err;
  }
}

module.exports = {
  openDb,
  withTransaction,
  nowIso,
};
