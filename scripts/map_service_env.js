#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');

function loadEnvFile(envFilePath = '.env') {
  const resolved = path.resolve(envFilePath);
  if (!fs.existsSync(resolved)) return { loaded: false, entries: {} };
  const lines = fs.readFileSync(resolved, 'utf8').split(/\r?\n/);
  const entries = {};
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const idx = line.indexOf('=');
    if (idx <= 0) continue;
    const key = line.slice(0, idx).trim();
    let value = line.slice(idx + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    entries[key] = value;
    if (!(key in process.env)) {
      process.env[key] = value;
    }
  }
  return { loaded: true, entries };
}

function resolveEnvVar(name, explicitValue = null, envFile = '.env') {
  loadEnvFile(envFile);
  return explicitValue || process.env[name] || null;
}

function resolveAmapKey(explicitKey = null, envFile = '.env') {
  return resolveEnvVar('AMAP_API_KEY', explicitKey, envFile);
}

module.exports = {
  loadEnvFile,
  resolveEnvVar,
  resolveAmapKey,
};
