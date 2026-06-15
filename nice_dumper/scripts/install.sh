#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-/Users/nice/Library/Android/sdk}}"
ADB="${ADB:-$SDK_DIR/platform-tools/adb}"
APK="$ROOT_DIR/dist/nice-dumper-debug.apk"
REMOTE_APK="/data/local/tmp/nice-dumper.apk"
REMOTE_LAUNCHER="/data/local/tmp/project"
LOCAL_LAUNCHER="$ROOT_DIR/build/project"

if [[ ! -x "$ADB" ]]; then
  echo "missing adb: $ADB" >&2
  exit 1
fi

if [[ ! -f "$APK" ]]; then
  "$ROOT_DIR/scripts/build.sh" >/dev/null
fi

if ! install_output="$("$ADB" install -r "$APK" 2>&1)"; then
  printf '%s\n' "$install_output" >&2
  echo "warning: adb install failed; continuing because app_process only needs the pushed APK." >&2
fi
"$ADB" push "$APK" "$REMOTE_APK" >/dev/null
"$ADB" shell su -c "chmod 0644 '$REMOTE_APK'"

mkdir -p "$(dirname "$LOCAL_LAUNCHER")"
printf '#!/system/bin/sh\nCLASSPATH=%s exec app_process / com.nice.dumper.Main "$@"\n' "$REMOTE_APK" > "$LOCAL_LAUNCHER"

"$ADB" push "$LOCAL_LAUNCHER" "$REMOTE_LAUNCHER" >/dev/null
"$ADB" shell su -c "chmod 0755 '$REMOTE_LAUNCHER'"

echo "installed launcher: $REMOTE_LAUNCHER"
echo "try: $ADB shell su -c '$REMOTE_LAUNCHER -d /sdcard/1.xml'"
