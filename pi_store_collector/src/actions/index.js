const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { AdbClient, sleep, shellQuote } = require('../device/adb_client');

const DEFAULT_RESOURCES_DIR = path.join(__dirname, 'resources');
const DEFAULT_DEVICE_PATH = process.env.PI_STORE_ACTION_DEVICE || '/dev/input/event3';
const DEFAULT_EVENT_STRUCT_BYTES = process.env.PI_STORE_ACTION_EVENT_BYTES
  ? Number(process.env.PI_STORE_ACTION_EVENT_BYTES)
  : 'auto';
const DEFAULT_WRITE_OVERHEAD_MS = Number(process.env.PI_STORE_ACTION_WRITE_OVERHEAD_MS || 0);
const DEFAULT_HELPER_DEVICE_PATH = process.env.PI_STORE_ACTION_HELPER || '/data/local/tmp/pi_input_replay';
const DEFAULT_REPLAY_MODE = process.env.PI_STORE_ACTION_REPLAY_MODE || 'helper';

const EVENT_TYPES = {
  EV_SYN: 0,
  EV_KEY: 1,
  EV_REL: 2,
  EV_ABS: 3,
  EV_MSC: 4,
  EV_SW: 5,
};

const EVENT_CODES = {
  SYN_REPORT: 0,
  SYN_MT_REPORT: 2,

  BTN_TOUCH: 330,
  BTN_TOOL_FINGER: 325,

  ABS_X: 0,
  ABS_Y: 1,
  ABS_MT_SLOT: 47,
  ABS_MT_TOUCH_MAJOR: 48,
  ABS_MT_TOUCH_MINOR: 49,
  ABS_MT_WIDTH_MAJOR: 50,
  ABS_MT_WIDTH_MINOR: 51,
  ABS_MT_ORIENTATION: 52,
  ABS_MT_POSITION_X: 53,
  ABS_MT_POSITION_Y: 54,
  ABS_MT_TOOL_TYPE: 55,
  ABS_MT_BLOB_ID: 56,
  ABS_MT_TRACKING_ID: 57,
  ABS_MT_PRESSURE: 58,
};

const cachedActionsByKey = new Map();

function parseNumericToken(token) {
  const text = String(token || '').trim();
  if (text === 'DOWN') return 1;
  if (text === 'UP') return 0;
  if (/^-?\d+$/.test(text)) return Number(text);
  if (/^-?0x[0-9a-f]+$/i.test(text)) return Number.parseInt(text, 16);
  if (/^[0-9a-f]+$/i.test(text)) {
    const value = Number.parseInt(text, 16);
    if (text.length === 8 && value > 0x7fffffff) return value - 0x100000000;
    return value;
  }
  return null;
}

function parseGeteventNumber(token, { signed = false } = {}) {
  const text = String(token || '').trim();
  if (text === 'DOWN') return 1;
  if (text === 'UP') return 0;

  const normalized = text.replace(/^0x/i, '');
  const shouldParseAsHex = /^-?0x[0-9a-f]+$/i.test(text)
    || /^[0-9a-f]+$/i.test(normalized) && (
      /[a-f]/i.test(normalized)
      || normalized.length === 4
      || normalized.length === 8
      || /^0[0-9]+$/.test(normalized)
    );

  if (shouldParseAsHex) {
    const value = Number.parseInt(normalized, 16);
    if (signed && normalized.length === 8 && value > 0x7fffffff) return value - 0x100000000;
    return value;
  }

  if (/^-?\d+$/.test(text)) return Number(text);
  return null;
}

function parseType(token) {
  if (Object.prototype.hasOwnProperty.call(EVENT_TYPES, token)) return EVENT_TYPES[token];
  return parseGeteventNumber(token);
}

function parseCode(token) {
  if (Object.prototype.hasOwnProperty.call(EVENT_CODES, token)) return EVENT_CODES[token];
  return parseGeteventNumber(token);
}

function parseGeteventLine(line) {
  const text = String(line || '').trim();
  if (!text) return null;

  const match = (
    text.match(/^(?:\[\s*(\d+(?:\.\d+)?)\]\s+)?([^:\s]+):\s+(\S+)\s+(\S+)\s+(\S+)/)
    || text.match(/^(?:\[\s*(\d+(?:\.\d+)?)\]\s+)?(\S+)\s+(\S+)\s+(\S+)/)
  );
  if (!match) return null;

  const hasDevicePath = match.length === 6;
  const timeText = match[1];
  const devicePath = hasDevicePath ? match[2] : null;
  const typeText = hasDevicePath ? match[3] : match[2];
  const codeText = hasDevicePath ? match[4] : match[3];
  const valueText = hasDevicePath ? match[5] : match[4];
  const type = parseType(typeText);
  const code = parseCode(codeText);
  const value = parseGeteventNumber(valueText, { signed: true });

  if (type === null || code === null || value === null) return null;

  return {
    time: timeText === undefined ? null : Number(timeText),
    devicePath,
    type,
    code,
    value,
  };
}

function clampDelayMs(delayMs, maxDelayMs) {
  if (maxDelayMs === null || maxDelayMs === undefined || maxDelayMs === Infinity) return delayMs;
  return Math.min(delayMs, maxDelayMs);
}

function clampDelaySeconds(delaySeconds, maxDelayMs) {
  if (maxDelayMs === null || maxDelayMs === undefined || maxDelayMs === Infinity) return delaySeconds;
  return Math.min(delaySeconds, maxDelayMs / 1000);
}

function parseActionLog(logText, { timeScale = 1, maxDelayMs = null } = {}) {
  const rawEvents = String(logText || '')
    .split(/\r?\n/)
    .map(parseGeteventLine)
    .filter(Boolean);

  const firstTimedEvent = rawEvents.find((event) => event.time !== null);
  const baseTime = firstTimedEvent ? firstTimedEvent.time : null;

  return rawEvents.map((event, index) => {
    const previous = rawEvents[index - 1];
    const elapsedSeconds = previous && event.time !== null && previous.time !== null
      ? Math.max(0, event.time - previous.time)
      : 0;
    const delaySeconds = clampDelaySeconds(elapsedSeconds * timeScale, maxDelayMs);
    const delayMs = clampDelayMs(Math.round(delaySeconds * 1000), maxDelayMs);

    return {
      delayMs,
      delaySeconds,
      atSeconds: baseTime === null || event.time === null ? null : Math.max(0, (event.time - baseTime) * timeScale),
      devicePath: event.devicePath,
      type: event.type,
      code: event.code,
      value: event.value,
    };
  });
}

function buildSendeventCommand({ devicePath, type, code, value, useRoot = true }) {
  const command = `sendevent ${devicePath} ${type} ${code} ${value}`;
  if (!useRoot) return command;
  return `su -c ${shellQuote(command)}`;
}

function buildReplayScript(events, { devicePath = DEFAULT_DEVICE_PATH } = {}) {
  const lines = ['#!/system/bin/sh', 'set -e'];

  for (const event of events) {
    if (event.delaySeconds > 0) {
      lines.push(`sleep ${event.delaySeconds.toFixed(6)}`);
    }
    const targetDevicePath = event.devicePath || devicePath;
    if (!targetDevicePath) {
      throw new Error('Action has events without a device path. Pass { devicePath: "/dev/input/eventX" } to act().');
    }
    lines.push(`sendevent ${targetDevicePath} ${event.type} ${event.code} ${event.value}`);
  }

  return `${lines.join('\n')}\n`;
}

function writeInt64Le(buffer, value, offset) {
  buffer.writeBigInt64LE(BigInt(value), offset);
}

function buildInputEventBuffer(events, { eventStructBytes = DEFAULT_EVENT_STRUCT_BYTES } = {}) {
  if (eventStructBytes === 'auto') {
    throw new Error('buildInputEventBuffer requires eventStructBytes to be 16 or 24.');
  }
  if (![16, 24].includes(eventStructBytes)) {
    throw new Error(`Unsupported input_event size ${eventStructBytes}. Use 16 for 32-bit Android or 24 for 64-bit Android.`);
  }

  const recordSize = eventStructBytes;
  const buffer = Buffer.alloc(events.length * recordSize);

  events.forEach((event, index) => {
    const offset = index * recordSize;
    if (eventStructBytes === 24) {
      writeInt64Le(buffer, 0, offset);
      writeInt64Le(buffer, 0, offset + 8);
      buffer.writeUInt16LE(event.type, offset + 16);
      buffer.writeUInt16LE(event.code, offset + 18);
      buffer.writeInt32LE(event.value, offset + 20);
    } else {
      buffer.writeInt32LE(0, offset);
      buffer.writeInt32LE(0, offset + 4);
      buffer.writeUInt16LE(event.type, offset + 8);
      buffer.writeUInt16LE(event.code, offset + 10);
      buffer.writeInt32LE(event.value, offset + 12);
    }
  });

  return buffer;
}

function writeUInt32Le(buffer, value, offset) {
  buffer.writeUInt32LE(Math.max(0, Math.min(0xffffffff, Math.round(value))), offset);
}

function buildReplayPacket(events) {
  const frames = groupEventsBySynReport(events);
  const chunks = [];
  const header = Buffer.from('PIAR1\0\0\0', 'binary');
  chunks.push(header);

  for (const frame of frames) {
    const frameHeader = Buffer.alloc(8);
    writeUInt32Le(frameHeader, frame.delaySeconds * 1000000, 0);
    writeUInt32Le(frameHeader, frame.events.length, 4);
    chunks.push(frameHeader);

    const body = Buffer.alloc(frame.events.length * 8);
    frame.events.forEach((event, index) => {
      const offset = index * 8;
      body.writeUInt16LE(event.type, offset);
      body.writeUInt16LE(event.code, offset + 2);
      body.writeInt32LE(event.value, offset + 4);
    });
    chunks.push(body);
  }

  return Buffer.concat(chunks);
}

function toOctalEscapes(buffer) {
  return Array.from(buffer, (byte) => `\\${byte.toString(8).padStart(3, '0')}`).join('');
}

function groupEventsByDelay(events) {
  const groups = [];
  for (const event of events) {
    if (!groups.length || event.delaySeconds > 0) {
      groups.push({ delaySeconds: event.delaySeconds, events: [event] });
    } else {
      groups[groups.length - 1].events.push(event);
    }
  }
  return groups;
}

function groupEventsBySynReport(events) {
  const frames = [];
  let current = [];
  let lastSynAtSeconds = null;

  for (const event of events) {
    current.push(event);
    if (event.type === EVENT_TYPES.EV_SYN && event.code === EVENT_CODES.SYN_REPORT) {
      const synAtSeconds = event.atSeconds;
      const delaySeconds = lastSynAtSeconds === null || synAtSeconds === null
        ? 0
        : Math.max(0, synAtSeconds - lastSynAtSeconds);
      frames.push({
        delaySeconds,
        events: current,
      });
      current = [];
      lastSynAtSeconds = synAtSeconds;
    }
  }

  if (current.length) {
    const firstAtSeconds = current.find((event) => event.atSeconds !== null)?.atSeconds;
    const delaySeconds = lastSynAtSeconds === null || firstAtSeconds === undefined
      ? 0
      : Math.max(0, firstAtSeconds - lastSynAtSeconds);
    frames.push({ delaySeconds, events: current });
  }

  return frames;
}

function appendBinaryWrite(lines, events, devicePath, { eventStructBytes, useAutoStructSize }) {
  if (useAutoStructSize) {
    const payload16 = toOctalEscapes(buildInputEventBuffer(events, { eventStructBytes: 16 }));
    const payload24 = toOctalEscapes(buildInputEventBuffer(events, { eventStructBytes: 24 }));
    lines.push(`if [ "$bits" = "32" ]; then printf '${payload16}' > ${devicePath}; else printf '${payload24}' > ${devicePath}; fi`);
    return;
  }

  const payload = toOctalEscapes(buildInputEventBuffer(events, { eventStructBytes }));
  lines.push(`printf '${payload}' > ${devicePath}`);
}

function buildBinaryReplayScript(events, {
  devicePath = DEFAULT_DEVICE_PATH,
  eventStructBytes = DEFAULT_EVENT_STRUCT_BYTES,
} = {}) {
  const lines = ['#!/system/bin/sh', 'set -e'];
  const useAutoStructSize = eventStructBytes === 'auto';
  if (useAutoStructSize) {
    lines.push('bits="$(getconf LONG_BIT 2>/dev/null || echo 64)"');
  }

  for (const group of groupEventsByDelay(events)) {
    if (group.delaySeconds > 0) {
      lines.push(`sleep ${group.delaySeconds.toFixed(6)}`);
    }

    const firstDevicePath = group.events[0].devicePath || devicePath;
    if (!firstDevicePath) {
      throw new Error('Action has events without a device path. Pass { devicePath: "/dev/input/eventX" } to act().');
    }

    const groupEvents = [];
    let currentDevicePath = firstDevicePath;
    for (const event of group.events) {
      const targetDevicePath = event.devicePath || devicePath;
      if (targetDevicePath !== currentDevicePath) {
        appendBinaryWrite(lines, groupEvents, currentDevicePath, { eventStructBytes, useAutoStructSize });
        groupEvents.length = 0;
        currentDevicePath = targetDevicePath;
      }
      groupEvents.push(event);
    }

    if (groupEvents.length) {
      appendBinaryWrite(lines, groupEvents, currentDevicePath, { eventStructBytes, useAutoStructSize });
    }
  }

  return `${lines.join('\n')}\n`;
}

function splitEventsByDevicePath(events, fallbackDevicePath) {
  const chunks = [];
  let current = null;
  for (const event of events) {
    const targetDevicePath = event.devicePath || fallbackDevicePath;
    if (!targetDevicePath) {
      throw new Error('Action has events without a device path. Pass { devicePath: "/dev/input/eventX" } to act().');
    }
    if (!current || current.devicePath !== targetDevicePath) {
      current = { devicePath: targetDevicePath, events: [] };
      chunks.push(current);
    }
    current.events.push(event);
  }
  return chunks;
}

function writeFrameFiles(events, {
  localFrameDir,
  devicePath = DEFAULT_DEVICE_PATH,
  eventStructBytes = DEFAULT_EVENT_STRUCT_BYTES,
} = {}) {
  fs.mkdirSync(localFrameDir, { recursive: true });
  const frames = [];

  groupEventsBySynReport(events).forEach((frame, frameIndex) => {
    const chunks = splitEventsByDevicePath(frame.events, devicePath).map((chunk, chunkIndex) => {
      const basename = `frame_${String(frameIndex).padStart(5, '0')}_${chunkIndex}.bin`;
      const localPath = path.join(localFrameDir, basename);
      const remoteName = `frames/${basename}`;
      if (eventStructBytes === 'auto') {
        fs.writeFileSync(`${localPath}.16`, buildInputEventBuffer(chunk.events, { eventStructBytes: 16 }));
        fs.writeFileSync(`${localPath}.24`, buildInputEventBuffer(chunk.events, { eventStructBytes: 24 }));
        return {
          devicePath: chunk.devicePath,
          remoteName16: `${remoteName}.16`,
          remoteName24: `${remoteName}.24`,
        };
      }

      fs.writeFileSync(localPath, buildInputEventBuffer(chunk.events, { eventStructBytes }));
      return {
        devicePath: chunk.devicePath,
        remoteName,
      };
    });

    frames.push({
      delaySeconds: frame.delaySeconds,
      chunks,
    });
  });

  return frames;
}

function buildFrameReplayScript(frames, {
  remoteActionDir,
  eventStructBytes = DEFAULT_EVENT_STRUCT_BYTES,
  writeOverheadMs = DEFAULT_WRITE_OVERHEAD_MS,
} = {}) {
  const lines = ['#!/system/bin/sh', 'set -e', `cd ${shellQuote(remoteActionDir)}`];
  const useAutoStructSize = eventStructBytes === 'auto';
  if (useAutoStructSize) {
    lines.push('bits="$(getconf LONG_BIT 2>/dev/null || echo 64)"');
  }

  for (const frame of frames) {
    const compensatedDelaySeconds = Math.max(0, frame.delaySeconds - (writeOverheadMs / 1000));
    if (compensatedDelaySeconds > 0) {
      lines.push(`sleep ${compensatedDelaySeconds.toFixed(6)}`);
    }
    for (const chunk of frame.chunks) {
      if (useAutoStructSize) {
        lines.push(`if [ "$bits" = "32" ]; then cat ${shellQuote(chunk.remoteName16)} > ${chunk.devicePath}; else cat ${shellQuote(chunk.remoteName24)} > ${chunk.devicePath}; fi`);
      } else {
        lines.push(`cat ${shellQuote(chunk.remoteName)} > ${chunk.devicePath}`);
      }
    }
  }

  return `${lines.join('\n')}\n`;
}

async function replayEvents(events, name, {
  adb = null,
  serial = '',
  runner = null,
  devicePath = DEFAULT_DEVICE_PATH,
  verbose = false,
  useRoot = true,
  replayMode = DEFAULT_REPLAY_MODE,
  deviceScriptPath = '',
  deviceActionDir = '',
  eventStructBytes = DEFAULT_EVENT_STRUCT_BYTES,
  writeOverheadMs = DEFAULT_WRITE_OVERHEAD_MS,
  helperDevicePath = DEFAULT_HELPER_DEVICE_PATH,
} = {}) {
  const client = adb || new AdbClient({ serial, runner });
  const startedAt = Date.now();
  if (verbose) {
    console.log(`Replaying action "${name}" with ${events.length} input events`);
  }

  if (replayMode === 'helper') {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-store-action-'));
    const localPacketPath = path.join(tmpDir, `${name}.piar`);
    const remotePacketPath = `/data/local/tmp/pi_store_action_${name}_${process.pid}.piar`;
    try {
      fs.writeFileSync(localPacketPath, buildReplayPacket(events));
      await client.push(localPacketPath, remotePacketPath);
      const replayCommand = `${helperDevicePath} ${devicePath} ${remotePacketPath}`;
      const command = useRoot
        ? `su -c ${shellQuote(replayCommand)}`
        : replayCommand;
      await client.shellCommand(command);
    } finally {
      await client.shellCommand(`rm -f ${shellQuote(remotePacketPath)}`).catch(() => { });
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  } else if (replayMode === 'binary') {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-store-action-'));
    const localActionDir = path.join(tmpDir, name);
    const localFrameDir = path.join(localActionDir, 'frames');
    const remoteActionDir = deviceActionDir || `/data/local/tmp/pi_store_action_${name}_${process.pid}`;
    const remoteScriptPath = `${remoteActionDir}/replay.sh`;
    try {
      const frames = writeFrameFiles(events, { localFrameDir, devicePath, eventStructBytes });
      const script = buildFrameReplayScript(frames, { remoteActionDir, eventStructBytes, writeOverheadMs });
      fs.writeFileSync(path.join(localActionDir, 'replay.sh'), script, 'utf8');
      await client.push(localActionDir, remoteActionDir);
      const command = useRoot
        ? `su -c ${shellQuote(`sh ${remoteScriptPath}`)}`
        : `sh ${shellQuote(remoteScriptPath)}`;
      await client.shellCommand(command);
    } finally {
      await client.shellCommand(`rm -rf ${shellQuote(remoteActionDir)}`).catch(() => { });
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  } else if (replayMode === 'script') {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-store-action-'));
    const localScriptPath = path.join(tmpDir, `${name}.sh`);
    const remoteScriptPath = deviceScriptPath || `/data/local/tmp/pi_store_action_${name}_${process.pid}.sh`;
    const script = buildReplayScript(events, { devicePath });
    fs.writeFileSync(localScriptPath, script, 'utf8');
    try {
      await client.push(localScriptPath, remoteScriptPath);
      const command = useRoot
        ? `su -c ${shellQuote(`sh ${remoteScriptPath}`)}`
        : `sh ${shellQuote(remoteScriptPath)}`;
      await client.shellCommand(command);
    } finally {
      await client.shellCommand(`rm -f ${shellQuote(remoteScriptPath)}`).catch(() => { });
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  } else if (replayMode === 'direct') {
    for (const event of events) {
      if (event.delayMs > 0) await sleep(event.delayMs);
      const targetDevicePath = event.devicePath || devicePath;
      if (!targetDevicePath) {
        throw new Error(`Action "${name}" has events without a device path. Pass { devicePath: "/dev/input/eventX" } to act().`);
      }
      await client.shellCommand(buildSendeventCommand({
        devicePath: targetDevicePath,
        type: event.type,
        code: event.code,
        value: event.value,
        useRoot,
      }));
    }
  } else {
    throw new Error(`Unknown replayMode "${replayMode}". Use "helper", "binary", "script", or "direct".`);
  }

  const result = {
    name,
    eventCount: events.length,
    durationMs: Date.now() - startedAt,
  };
  if (verbose) {
    console.log(`Finished action "${name}" (${result.eventCount} input events, ${result.durationMs}ms)`);
  }
  return result;
}

function createAction(name, events) {
  const action = async (options = {}) => replayEvents(events, name, options);
  action.name = name;
  action.events = events;
  action.eventCount = events.length;
  return action;
}

function parse({ resourcesDir = DEFAULT_RESOURCES_DIR, timeScale = 1, maxDelayMs = null } = {}) {
  const cacheKey = JSON.stringify({ resourcesDir: path.resolve(resourcesDir), timeScale, maxDelayMs });
  if (cachedActionsByKey.has(cacheKey)) return cachedActionsByKey.get(cacheKey);

  const actions = {};
  const entries = fs.existsSync(resourcesDir)
    ? fs.readdirSync(resourcesDir, { withFileTypes: true })
    : [];

  for (const entry of entries) {
    if (!entry.isFile() || path.extname(entry.name) !== '.log') continue;
    const actionName = path.basename(entry.name, '.log');
    const logPath = path.join(resourcesDir, entry.name);
    const logText = fs.readFileSync(logPath, 'utf8');
    const events = parseActionLog(logText, { timeScale, maxDelayMs });
    actions[actionName] = createAction(actionName, events);
  }

  cachedActionsByKey.set(cacheKey, actions);
  return actions;
}

async function act(name, {
  adb = null,
  serial = '',
  runner = null,
  resourcesDir = DEFAULT_RESOURCES_DIR,
  devicePath = DEFAULT_DEVICE_PATH,
  timeScale = 1,
  maxDelayMs = null,
  verbose = false,
  useRoot = true,
  replayMode = DEFAULT_REPLAY_MODE,
  deviceScriptPath = '',
  deviceActionDir = '',
  eventStructBytes = DEFAULT_EVENT_STRUCT_BYTES,
  writeOverheadMs = DEFAULT_WRITE_OVERHEAD_MS,
  helperDevicePath = DEFAULT_HELPER_DEVICE_PATH,
} = {}) {
  const actions = parse({ resourcesDir, timeScale, maxDelayMs });
  const action = actions[name];
  if (!action) {
    const available = Object.keys(actions).sort().join(', ') || 'none';
    throw new Error(`Unknown action "${name}". Available actions: ${available}`);
  }

  return action({
    adb,
    serial,
    runner,
    devicePath,
    verbose,
    useRoot,
    replayMode,
    deviceScriptPath,
    deviceActionDir,
    eventStructBytes,
    writeOverheadMs,
    helperDevicePath,
  });
}

module.exports = {
  act,
  parse,
  parseActionLog,
  parseGeteventLine,
  buildSendeventCommand,
  buildReplayScript,
  buildBinaryReplayScript,
  buildInputEventBuffer,
  buildReplayPacket,
  buildFrameReplayScript,
  groupEventsBySynReport,
  writeFrameFiles,
  DEFAULT_RESOURCES_DIR,
  DEFAULT_DEVICE_PATH,
  DEFAULT_EVENT_STRUCT_BYTES,
  DEFAULT_WRITE_OVERHEAD_MS,
  DEFAULT_HELPER_DEVICE_PATH,
  DEFAULT_REPLAY_MODE,
};

act('touch')