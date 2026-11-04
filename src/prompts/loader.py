"""Resolve prompt files without depending on configuration, models, or agents."""

from collections.abc import Iterator, Mapping
from pathlib import Path

PROMPT_ROOT = Path(__file__).parent
BASELINE_MODEL = "gpt-6.1-sol"


def load_prompt(name: str, profile: Mapping[str, object] | None) -> str:
    """Read the model override, its explicit affinity, or the baseline model."""
    _validate_segment(name)
    for path in _candidates(f"{name}.md", profile):
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        if not content.strip():
            raise ValueError(f"Prompt file is empty: {path}")
        return content
    raise FileNotFoundError(f"No prompt configured for {name}")


def resolve_page_mechanism(profile: Mapping[str, object] | None) -> Path:
    """Select one complete page mechanism tree for reading, editing, and execution."""
    for path in _candidates("page_mechanism", profile):
        if path.is_dir():
            return path
        if path.exists():
            raise ValueError(f"Page mechanism must be a directory: {path}")
    raise FileNotFoundError("No page mechanism configured")


def _candidates(asset: str, profile: Mapping[str, object] | None) -> Iterator[Path]:
    profile = profile or {}
    candidates: list[str] = []
    for key in ("prompt_model", "prompt_affinity"):
        value = profile.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a directory name")
            _validate_segment(value)
            candidates.append(value)
    candidates.append(BASELINE_MODEL)
    root = PROMPT_ROOT.resolve()
    for candidate in dict.fromkeys(candidates):
        path = (root / candidate / asset).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Prompt path must stay within the prompt directory")
        yield path


def _validate_segment(value: str) -> None:
    if not value.strip() or value in {".", ".."} or any(char in value for char in ("/", "\\", "\0")):
        raise ValueError("Prompt names must be single directory or file names")
