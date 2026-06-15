#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-/Users/nice/Library/Android/sdk}}"
PLATFORM="${ANDROID_PLATFORM:-android-35}"
BUILD_TOOLS="${ANDROID_BUILD_TOOLS:-35.0.0}"
DEFAULT_JDK_HOME="$HOME/.local/share/jdks/temurin-17/Contents/Home"

if [[ -z "${JAVA_HOME:-}" && -x "$DEFAULT_JDK_HOME/bin/javac" ]]; then
  export JAVA_HOME="$DEFAULT_JDK_HOME"
fi

if [[ -n "${JAVA_HOME:-}" ]]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi

ANDROID_JAR="$SDK_DIR/platforms/$PLATFORM/android.jar"
BUILD_TOOLS_DIR="$SDK_DIR/build-tools/$BUILD_TOOLS"
AAPT2="$BUILD_TOOLS_DIR/aapt2"
D8="$BUILD_TOOLS_DIR/d8"
APKSIGNER="$BUILD_TOOLS_DIR/apksigner"
ZIPALIGN="$BUILD_TOOLS_DIR/zipalign"

BUILD_DIR="$ROOT_DIR/build"
DIST_DIR="$ROOT_DIR/dist"
CLASSES_DIR="$BUILD_DIR/classes"
DEX_DIR="$BUILD_DIR/dex"
APK_UNSIGNED="$BUILD_DIR/nice-dumper-unsigned.apk"
APK_ALIGNED="$BUILD_DIR/nice-dumper-aligned.apk"
APK_SIGNED="$DIST_DIR/nice-dumper-debug.apk"
CLASSES_JAR="$BUILD_DIR/classes.jar"
KEYSTORE="$BUILD_DIR/debug.keystore"

find_jdk_tool() {
  local name="$1"
  if [[ -n "${JAVA_HOME:-}" && -x "$JAVA_HOME/bin/$name" ]]; then
    echo "$JAVA_HOME/bin/$name"
    return 0
  fi
  if command -v "$name" >/dev/null 2>&1; then
    command -v "$name"
    return 0
  fi
  return 1
}

JAVAC="$(find_jdk_tool javac || true)"
KEYTOOL="$(find_jdk_tool keytool || true)"
JAR="$(find_jdk_tool jar || true)"

if [[ -z "$JAVAC" ]] || ! "$JAVAC" -version >/dev/null 2>&1; then
  echo "missing usable javac. Install a JDK or set JAVA_HOME to a JDK path." >&2
  exit 1
fi

if [[ -z "$KEYTOOL" ]] || ! "$KEYTOOL" -help >/dev/null 2>&1; then
  echo "missing usable keytool. Install a JDK or set JAVA_HOME to a JDK path." >&2
  exit 1
fi

if [[ -z "$JAR" ]] || ! "$JAR" --help >/dev/null 2>&1; then
  echo "missing usable jar. Install a JDK or set JAVA_HOME to a JDK path." >&2
  exit 1
fi

if ! command -v zip >/dev/null 2>&1; then
  echo "missing tool: zip" >&2
  exit 1
fi

for tool in "$AAPT2" "$D8" "$APKSIGNER" "$ZIPALIGN"; do
  if [[ ! -x "$tool" ]]; then
    echo "missing tool: $tool" >&2
    exit 1
  fi
done

if [[ ! -f "$ANDROID_JAR" ]]; then
  echo "missing android.jar: $ANDROID_JAR" >&2
  exit 1
fi

rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$CLASSES_DIR" "$DEX_DIR" "$DIST_DIR"

find "$ROOT_DIR/src" -name '*.java' -print > "$BUILD_DIR/sources.list"

"$JAVAC" \
  -source 8 \
  -target 8 \
  -bootclasspath "$ANDROID_JAR" \
  -d "$CLASSES_DIR" \
  @"$BUILD_DIR/sources.list"

"$JAR" cf "$CLASSES_JAR" -C "$CLASSES_DIR" .

"$D8" \
  --lib "$ANDROID_JAR" \
  --min-api 23 \
  --output "$DEX_DIR" \
  "$CLASSES_JAR"

"$AAPT2" link \
  --manifest "$ROOT_DIR/AndroidManifest.xml" \
  -I "$ANDROID_JAR" \
  -o "$APK_UNSIGNED"

cd "$DEX_DIR"
zip -q -u "$APK_UNSIGNED" classes.dex
cd "$ROOT_DIR"

"$ZIPALIGN" -f 4 "$APK_UNSIGNED" "$APK_ALIGNED"

if [[ ! -f "$KEYSTORE" ]]; then
  "$KEYTOOL" -genkeypair \
    -keystore "$KEYSTORE" \
    -storepass android \
    -keypass android \
    -alias androiddebugkey \
    -keyalg RSA \
    -keysize 2048 \
    -validity 10000 \
    -dname "CN=Android Debug,O=Android,C=US" >/dev/null
fi

"$APKSIGNER" sign \
  --ks "$KEYSTORE" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out "$APK_SIGNED" \
  "$APK_ALIGNED"

"$APKSIGNER" verify "$APK_SIGNED"

echo "$APK_SIGNED"
