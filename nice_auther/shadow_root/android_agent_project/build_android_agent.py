"""Build entry point for the Android shadow agent jar.

This produces an app_process-compatible jar containing classes.dex:

    javac -> d8 -> jar classes.dex
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parent
    sdk = _find_android_sdk()
    if sdk is None:
        print("ANDROID_HOME or ANDROID_SDK_ROOT is required, or install the SDK at ~/Library/Android/sdk", file=sys.stderr)
        return 2
    android_jar = _latest_android_jar(sdk)
    if android_jar is None:
        print(f"no Android platform android.jar found under {sdk}", file=sys.stderr)
        return 2
    d8 = _latest_d8(sdk)
    if d8 is None:
        print(f"no Android build-tools d8 found under {sdk}", file=sys.stderr)
        return 2
    jdk_home = _find_jdk_home()
    javac = _find_java_tool("javac", jdk_home)
    jar = _find_java_tool("jar", jdk_home)
    if not javac or not jar:
        print("javac and jar are required to build nice_shadow_agent.jar", file=sys.stderr)
        return 2
    env = os.environ.copy()
    if jdk_home is not None:
        env["JAVA_HOME"] = str(jdk_home)
        env["PATH"] = f"{jdk_home / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    build_dir = root / "build" / "classes"
    dex_dir = root / "build" / "dex"
    output = root / "build" / "nice_shadow_agent.jar"
    _clean_dir(build_dir)
    _clean_dir(dex_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    dex_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted(str(path) for path in (root / "src").rglob("*.java"))
    if not sources:
        print("no Android agent Java sources found", file=sys.stderr)
        return 2
    subprocess.run(
        [
            javac,
            "-source",
            "8",
            "-target",
            "8",
            "-classpath",
            str(android_jar),
            "-d",
            str(build_dir),
            *sources,
        ],
        check=True,
        env=env,
    )
    class_files = sorted(str(path) for path in build_dir.rglob("*.class"))
    if not class_files:
        print("javac produced no .class files", file=sys.stderr)
        return 2
    subprocess.run([str(d8), "--lib", str(android_jar), "--output", str(dex_dir), *class_files], check=True, env=env)
    classes_dex = dex_dir / "classes.dex"
    if not classes_dex.exists():
        print("d8 produced no classes.dex", file=sys.stderr)
        return 2
    if output.exists():
        output.unlink()
    subprocess.run([jar, "cf", str(output), "-C", str(dex_dir), "classes.dex"], check=True, env=env)
    print(output)
    return 0


def _find_android_sdk() -> Path | None:
    candidates = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        str(Path.home() / "Library" / "Android" / "sdk"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


def _latest_android_jar(sdk: Path) -> Path | None:
    platforms = sdk / "platforms"
    candidates = sorted(platforms.glob("android-*/android.jar"))
    return candidates[-1] if candidates else None


def _latest_d8(sdk: Path) -> Path | None:
    build_tools = sdk / "build-tools"
    candidates = sorted(path for path in build_tools.glob("*/d8") if path.is_file())
    return candidates[-1] if candidates else None


def _find_jdk_home() -> Path | None:
    candidates = [
        os.environ.get("JAVA_HOME"),
        "/Applications/Android Studio.app/Contents/jbr/Contents/Home",
        "/Applications/Android Studio.app/Contents/jre/Contents/Home",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if (path / "bin" / "javac").exists() and (path / "bin" / "jar").exists():
            return path
    return None


def _find_java_tool(name: str, jdk_home: Path | None) -> str | None:
    if jdk_home is not None:
        tool = jdk_home / "bin" / name
        if tool.exists():
            return str(tool)
    return shutil.which(name)


def _clean_dir(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
