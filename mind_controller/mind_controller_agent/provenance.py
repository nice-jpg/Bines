"""Path-scoped Git commits for prompt revisions."""

from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class PromptCommitError(RuntimeError):
    """Raised when a prompt-only commit cannot be created safely."""


@dataclass(frozen=True)
class PromptCommit:
    repository: str
    commit: str
    paths: tuple[str, ...]


def commit_prompt_files(paths: Iterable[Path], message: str) -> list[PromptCommit]:
    """Commit only the selected tracked prompt files, grouped by repository."""

    grouped: dict[Path, list[Path]] = defaultdict(list)
    for path in sorted({item.resolve() for item in paths}):
        root = _repository_root(path)
        grouped[root].append(path)

    commits: list[PromptCommit] = []
    for root, repo_paths in grouped.items():
        relative_paths = tuple(str(path.relative_to(root)) for path in repo_paths)
        for relative_path in relative_paths:
            result = _git(root, "ls-files", "--error-unmatch", "--", relative_path, check=False)
            if result.returncode != 0:
                raise PromptCommitError(
                    f"Declared prompt file must already be tracked by Git: {root / relative_path}"
                )
        status = _git(
            root,
            "status",
            "--porcelain",
            "--untracked-files=no",
            "--",
            *relative_paths,
        ).stdout
        if not status.strip():
            continue
        _git(
            root,
            "commit",
            "--only",
            "--no-verify",
            "--no-gpg-sign",
            "-m",
            message,
            "--",
            *relative_paths,
        )
        commit_hash = _git(root, "rev-parse", "HEAD").stdout.strip()
        committed_paths = tuple(
            line
            for line in _git(
                root,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "HEAD",
            ).stdout.splitlines()
            if line
        )
        unexpected = sorted(set(committed_paths) - set(relative_paths))
        if unexpected:
            raise PromptCommitError(
                f"Prompt commit unexpectedly included non-prompt paths: {unexpected}"
            )
        commits.append(
            PromptCommit(
                repository=str(root),
                commit=commit_hash,
                paths=committed_paths,
            )
        )
    return commits


def _repository_root(path: Path) -> Path:
    result = _git(path.parent, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise PromptCommitError(f"Declared prompt file is not inside a Git repository: {path}")
    return Path(result.stdout.strip()).resolve()


def _git(
    cwd: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise PromptCommitError(output or f"git {' '.join(args)} failed")
    return result

