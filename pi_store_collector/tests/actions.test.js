const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const {
  act,
  buildBinaryReplayScript,
  buildFrameReplayScript,
  buildInputEventBuffer,
  buildReplayScript,
  buildReplayPacket,
  buildSendeventCommand,
  groupEventsBySynReport,
  parse,
  parseActionLog,
  parseGeteventLine,
  writeFrameFiles,
} = require('../src/actions');

test('buildSendeventCommand uses root by default', () => {
  assert.equal(
    buildSendeventCommand({ devicePath: '/dev/input/event3', type: 1, code: 330, value: 1 }),
    "su -c 'sendevent /dev/input/event3 1 330 1'",
  );
  assert.equal(
    buildSendeventCommand({ devicePath: '/dev/input/event3', type: 1, code: 330, value: 1, useRoot: false }),
    'sendevent /dev/input/event3 1 330 1',
  );
});

test('parseGeteventLine supports symbolic getevent -lt output', () => {
  const event = parseGeteventLine('[ 12345.678901] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 000003a1');

  assert.deepEqual(event, {
    time: 12345.678901,
    devicePath: '/dev/input/event2',
    type: 3,
    code: 53,
    value: 929,
  });
});

test('parseGeteventLine treats getevent numeric fields as hex', () => {
  assert.equal(parseGeteventLine('[ 1.000000] EV_ABS ABS_MT_TOUCH_MINOR 00000060').value, 96);
  assert.equal(parseGeteventLine('[ 1.000000] EV_ABS ABS_MT_PRESSURE 00000024').value, 36);

  assert.deepEqual(
    parseGeteventLine('[ 1.000000] /dev/input/event2: 0003 0035 000003a1'),
    {
      time: 1,
      devicePath: '/dev/input/event2',
      type: 3,
      code: 53,
      value: 929,
    },
  );
});

test('parseGeteventLine supports pathless getevent output with key states', () => {
  assert.deepEqual(
    parseGeteventLine('[   32786.134342] EV_KEY       BTN_TOUCH            DOWN'),
    {
      time: 32786.134342,
      devicePath: null,
      type: 1,
      code: 330,
      value: 1,
    },
  );

  assert.equal(parseGeteventLine('[   32789.549191] EV_ABS       ABS_MT_TRACKING_ID   ffffffff').value, -1);
  assert.equal(parseGeteventLine('[   32789.549191] EV_KEY       BTN_TOUCH            UP').value, 0);
});

test('parseActionLog preserves delays and sendevent payloads', () => {
  const events = parseActionLog(`
[ 1.000000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000001
[ 1.025000] /dev/input/event2: EV_ABS ABS_MT_POSITION_X 000003a1
[ 2.177000] /dev/input/event2: EV_SYN SYN_REPORT 00000000
`);

  assert.equal(events.length, 3);
  assert.equal(events[0].delayMs, 0);
  assert.equal(events[1].delayMs, 25);
  assert.equal(events[2].delayMs, 1152);
  assert.ok(Math.abs(events[1].atSeconds - 0.025) < 0.000001);
  assert.equal(events[2].type, 0);
  assert.equal(events[2].code, 0);
});

test('buildReplayScript keeps timestamp delays on device side', () => {
  const events = parseActionLog(`
[ 1.000000] EV_KEY       BTN_TOUCH            DOWN
[ 1.025000] EV_SYN       SYN_REPORT           00000000
`);

  assert.equal(
    buildReplayScript(events, { devicePath: '/dev/input/event5' }),
    [
      '#!/system/bin/sh',
      'set -e',
      'sendevent /dev/input/event5 1 330 1',
      'sleep 0.025000',
      'sendevent /dev/input/event5 0 0 0',
      '',
    ].join('\n'),
  );
});

test('buildInputEventBuffer writes linux input_event records', () => {
  const buffer = buildInputEventBuffer([{ type: 1, code: 330, value: 1 }], { eventStructBytes: 24 });

  assert.equal(buffer.length, 24);
  assert.equal(buffer.readUInt16LE(16), 1);
  assert.equal(buffer.readUInt16LE(18), 330);
  assert.equal(buffer.readInt32LE(20), 1);
});

test('buildReplayPacket writes compact helper frames', () => {
  const events = parseActionLog(`
[ 1.000000] EV_KEY       BTN_TOUCH            DOWN
[ 1.000000] EV_SYN       SYN_REPORT           00000000
[ 1.025000] EV_KEY       BTN_TOUCH            UP
[ 1.025000] EV_SYN       SYN_REPORT           00000000
`);
  const packet = buildReplayPacket(events);

  assert.equal(packet.subarray(0, 8).toString('binary'), 'PIAR1\0\0\0');
  assert.equal(packet.readUInt32LE(8), 0);
  assert.equal(packet.readUInt32LE(12), 2);
  assert.equal(packet.readUInt16LE(16), 1);
  assert.equal(packet.readUInt16LE(18), 330);
  assert.equal(packet.readInt32LE(20), 1);
  assert.equal(packet.readUInt32LE(32), 25000);
  assert.equal(packet.readUInt32LE(36), 2);
});

test('buildBinaryReplayScript groups same-timestamp events into binary writes', () => {
  const events = parseActionLog(`
[ 1.000000] EV_KEY       BTN_TOUCH            DOWN
[ 1.000000] EV_SYN       SYN_REPORT           00000000
[ 1.025000] EV_KEY       BTN_TOUCH            UP
`);
  const script = buildBinaryReplayScript(events, { devicePath: '/dev/input/event5', eventStructBytes: 24 });

  assert.match(script, /^#!\/system\/bin\/sh\nset -e\n/);
  assert.match(script, /printf '.*' > \/dev\/input\/event5/);
  assert.match(script, /sleep 0\.025000/);
  assert.equal((script.match(/printf/g) || []).length, 2);
  assert.equal(script.includes('sendevent'), false);
});

test('groupEventsBySynReport anchors delays to SYN_REPORT frames', () => {
  const events = parseActionLog(`
[ 1.000000] EV_ABS       ABS_MT_POSITION_X    00000010
[ 1.000000] EV_SYN       SYN_REPORT           00000000
[ 1.005000] EV_ABS       ABS_MT_POSITION_X    00000011
[ 1.006000] EV_ABS       ABS_MT_POSITION_Y    00000012
[ 1.006000] EV_SYN       SYN_REPORT           00000000
`);
  const frames = groupEventsBySynReport(events);

  assert.equal(frames.length, 2);
  assert.equal(frames[0].delaySeconds, 0);
  assert.equal(frames[0].events.length, 2);
  assert.ok(Math.abs(frames[1].delaySeconds - 0.006) < 0.000001);
  assert.equal(frames[1].events.length, 3);
});

test('writeFrameFiles and buildFrameReplayScript replay binary frames by SYN_REPORT', () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-actions-frames-'));
  try {
    const localFrameDir = path.join(tmpDir, 'frames');
    const events = parseActionLog(`
[ 1.000000] EV_KEY       BTN_TOUCH            DOWN
[ 1.000000] EV_SYN       SYN_REPORT           00000000
[ 1.025000] EV_KEY       BTN_TOUCH            UP
[ 1.025000] EV_SYN       SYN_REPORT           00000000
`);

    const frames = writeFrameFiles(events, {
      localFrameDir,
      devicePath: '/dev/input/event5',
      eventStructBytes: 24,
    });
    const script = buildFrameReplayScript(frames, {
      remoteActionDir: '/data/local/tmp/touch_replay',
      eventStructBytes: 24,
      writeOverheadMs: 2,
    });

    assert.equal(frames.length, 2);
    assert.equal(fs.existsSync(path.join(localFrameDir, 'frame_00000_0.bin')), true);
    assert.equal(fs.statSync(path.join(localFrameDir, 'frame_00000_0.bin')).size, 48);
    assert.match(script, /cd '\/data\/local\/tmp\/touch_replay'/);
    assert.match(script, /cat 'frames\/frame_00000_0\.bin' > \/dev\/input\/event5/);
    assert.match(script, /sleep 0\.023000/);
    assert.equal(script.includes('printf'), false);
    assert.equal(script.includes('sendevent'), false);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('act parses resources once and replays cached action events', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-actions-'));
  const calls = [];
  const adb = {
    shellCommand: async (command) => {
      calls.push(command);
    },
  };

  try {
    fs.writeFileSync(
      path.join(tmpDir, 'slide_down.log'),
      [
        '[ 1.000000] /dev/input/event2: EV_ABS ABS_MT_TRACKING_ID 00000001',
        '[ 1.000000] /dev/input/event2: EV_SYN SYN_REPORT 00000000',
      ].join('\n'),
      'utf8',
    );

    const actions = parse({ resourcesDir: tmpDir });
    assert.deepEqual(Object.keys(actions), ['slide_down']);
    assert.equal(typeof actions.slide_down, 'function');
    assert.equal(Array.isArray(actions.slide_down), false);
    assert.equal(actions.slide_down.eventCount, 2);

    await act('slide_down', { adb, resourcesDir: tmpDir, replayMode: 'direct' });
    fs.writeFileSync(path.join(tmpDir, 'slide_down.log'), '', 'utf8');
    const result = await act('slide_down', { adb, resourcesDir: tmpDir, replayMode: 'direct' });

    assert.deepEqual(calls, [
      "su -c 'sendevent /dev/input/event2 3 57 1'",
      "su -c 'sendevent /dev/input/event2 0 0 0'",
      "su -c 'sendevent /dev/input/event2 3 57 1'",
      "su -c 'sendevent /dev/input/event2 0 0 0'",
    ]);
    assert.equal(result.name, 'slide_down');
    assert.equal(result.eventCount, 2);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('act replays pathless resource logs with fallback device path', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-actions-pathless-'));
  const calls = [];
  const adb = {
    shellCommand: async (command) => {
      calls.push(command);
    },
  };

  try {
    fs.writeFileSync(
      path.join(tmpDir, 'touch.log'),
      [
        '[ 1.000000] EV_KEY       BTN_TOUCH            DOWN',
        '[ 1.000000] EV_SYN       SYN_REPORT           00000000',
      ].join('\n'),
      'utf8',
    );

    await act('touch', { adb, resourcesDir: tmpDir, devicePath: '/dev/input/event5', replayMode: 'direct' });

    assert.deepEqual(calls, [
      "su -c 'sendevent /dev/input/event5 1 330 1'",
      "su -c 'sendevent /dev/input/event5 0 0 0'",
    ]);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('act can use pushed binary device-side script fallback', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-actions-script-'));
  const calls = [];
  const adb = {
    push: async (localPath, devicePath) => {
      calls.push({
        kind: 'push',
        localPath,
        devicePath,
        script: fs.readFileSync(path.join(localPath, 'replay.sh'), 'utf8'),
      });
    },
    shellCommand: async (command) => {
      calls.push({ kind: 'shell', command });
    },
  };

  try {
    fs.writeFileSync(
      path.join(tmpDir, 'touch.log'),
      [
        '[ 1.000000] EV_KEY       BTN_TOUCH            DOWN',
        '[ 1.025000] EV_SYN       SYN_REPORT           00000000',
      ].join('\n'),
      'utf8',
    );

    const result = await act('touch', {
      adb,
      resourcesDir: tmpDir,
      replayMode: 'binary',
      devicePath: '/dev/input/event5',
      deviceActionDir: '/data/local/tmp/touch_replay',
    });

    assert.equal(result.eventCount, 2);
    assert.equal(calls[0].kind, 'push');
    assert.equal(calls[0].devicePath, '/data/local/tmp/touch_replay');
    assert.match(calls[0].script, /cat 'frames\/frame_00000_0\.bin\.24' > \/dev\/input\/event5/);
    assert.equal(calls[0].script.includes('printf'), false);
    assert.equal(calls[0].script.includes('sendevent'), false);
    assert.deepEqual(calls.slice(1), [
      { kind: 'shell', command: "su -c 'sh /data/local/tmp/touch_replay/replay.sh'" },
      { kind: 'shell', command: "rm -rf '/data/local/tmp/touch_replay'" },
    ]);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('act helper mode pushes one packet and runs native helper once by default', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-actions-helper-'));
  const calls = [];
  const adb = {
    push: async (localPath, devicePath) => {
      calls.push({
        kind: 'push',
        localPath,
        devicePath,
        packet: fs.readFileSync(localPath),
      });
    },
    shellCommand: async (command) => {
      calls.push({ kind: 'shell', command });
    },
  };

  try {
    fs.writeFileSync(
      path.join(tmpDir, 'touch.log'),
      [
        '[ 1.000000] EV_KEY       BTN_TOUCH            DOWN',
        '[ 1.000000] EV_SYN       SYN_REPORT           00000000',
      ].join('\n'),
      'utf8',
    );

    const result = await act('touch', {
      adb,
      resourcesDir: tmpDir,
      devicePath: '/dev/input/event5',
      helperDevicePath: '/data/local/tmp/pi_input_replay',
    });

    assert.equal(result.eventCount, 2);
    assert.equal(calls[0].kind, 'push');
    assert.match(calls[0].devicePath, /\/data\/local\/tmp\/pi_store_action_touch_\d+\.piar/);
    assert.equal(calls[0].packet.subarray(0, 8).toString('binary'), 'PIAR1\0\0\0');
    assert.match(calls[1].command, /^su -c '\/data\/local\/tmp\/pi_input_replay \/dev\/input\/event5 \/data\/local\/tmp\/pi_store_action_touch_\d+\.piar'$/);
    assert.match(calls[2].command, /^rm -f '\/data\/local\/tmp\/pi_store_action_touch_\d+\.piar'$/);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
