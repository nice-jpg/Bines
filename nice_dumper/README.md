# nice_dumper

`nice_dumper` is a minimal Android root command tool for dumping the current
UIAutomator hierarchy XML from the device shell.

It does not use Gradle. The build script uses the local Android SDK directly:
`javac`, `d8`, `aapt2`, `zipalign`, and `apksigner`.

## Build

```bash
cd /Users/nice/Project/misc/Bines/nice_dumper
scripts/build.sh
```

Defaults:

- SDK: `/Users/nice/Library/Android/sdk`
- platform: `android-35`
- build tools: `35.0.0`
- JDK fallback: `/Users/nice/.local/share/jdks/temurin-17/Contents/Home`

Override them if needed:

```bash
ANDROID_SDK_ROOT=/path/to/sdk ANDROID_PLATFORM=android-35 ANDROID_BUILD_TOOLS=35.0.0 scripts/build.sh
```

The signed APK is written to:

```text
dist/nice-dumper-debug.apk
```

## Install

The install script pushes the APK to `/data/local/tmp/nice-dumper.apk` and
creates a root shell launcher at `/data/local/tmp/project`.

```bash
scripts/install.sh
```

Run on the rooted device:

```bash
adb shell su -c '/data/local/tmp/project -d /sdcard/1.xml'
```

If you need the bare command name `project`, copy or symlink the launcher into a
directory on the root shell `PATH`, for example:

```bash
adb shell su -c 'mount -o rw,remount /system && cp /data/local/tmp/project /system/bin/project && chmod 0755 /system/bin/project'
adb shell su -c 'project -d /sdcard/1.xml'
```

Use the `/data/local/tmp/project` launcher when `/system` is read-only.

## Usage

```text
Usage: project -d <path> [--compressed] [--timeout-ms <ms>]

Options:
  -d, --dump <path>       Save current UIAutomator XML to this path.
      --compressed        Prefer uiautomator dump --compressed.
      --timeout-ms <ms>   Command timeout. Default: 15000.
  -h, --help              Show this help.
```

The tool first connects to `UiAutomation` directly and serializes the current
`AccessibilityNodeInfo` tree, so animated pages do not have to reach the idle
state required by the platform `uiautomator dump` command. If direct
`UiAutomation` is unavailable, it falls back to file-based `uiautomator dump`
and then `uiautomator dump /dev/tty`.

Final output is written through a temporary file in the target directory and
then renamed into place. This avoids leaving a half-written XML file when a dump
or write fails.

## Exit Codes

- `0`: success
- `2`: invalid arguments
- `3`: UI dump failed
- `4`: output file write failed

## Notes

- The command must run as root.
- The device must provide the system `uiautomator` command.
- Animated screens are captured at one available moment; this tool does not
  record animation frames.
- UIAutomator may miss WebView, game, canvas, or custom-rendered text. OCR is
  intentionally out of scope for v1.
