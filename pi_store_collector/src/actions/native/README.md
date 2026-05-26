# pi_input_replay

`pi_input_replay` is the fast replay helper for action logs.

It runs as one native process on the Android device, opens `/dev/input/eventX`
once, then replays a compact packet with `nanosleep()` and `write()`. This avoids
per-frame shell `sleep`, `cat`, `printf`, or `sendevent` overhead.

Build on a machine with Android NDK:

```bash
$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/darwin-x86_64/bin/aarch64-linux-android21-clang \
  -O2 -static -o pi_input_replay pi_input_replay.c
```

For Apple Silicon NDK installs, the prebuilt folder may still be `darwin-x86_64`.

Install on the rooted device:

```bash
adb push pi_input_replay /data/local/tmp/pi_input_replay
adb shell chmod 755 /data/local/tmp/pi_input_replay
```

Use from JS:

```js
await act('touch', {
  replayMode: 'helper',
  devicePath: '/dev/input/event3',
  helperDevicePath: '/data/local/tmp/pi_input_replay',
});
```
