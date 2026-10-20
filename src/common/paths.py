"""Application-owned filesystem locations."""

from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT: Final[Path] = REPOSITORY_ROOT / "workspace"
WORKSPACE_ENV_PATH: Final[Path] = WORKSPACE_ROOT / ".env"
