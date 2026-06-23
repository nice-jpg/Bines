"""Build the local nice_auther WebRTC gateway binary."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parent
    output = root / "build" / "nice-webrtc-gateway"
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("GOCACHE", "/private/tmp/bines_gocache")
    subprocess.run(
        [
            "go",
            "build",
            "-o",
            str(output),
            "./nice_auther/shadow_root/webrtc_gateway/cmd/nice-webrtc-gateway",
        ],
        cwd=root.parents[2],
        env=env,
        check=True,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
