"""Import bridge for the existing src/device package."""

from __future__ import annotations

from pathlib import Path
import sys


SRC_PATH = Path(__file__).resolve().parents[2] / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

