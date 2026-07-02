"""Prompt-specific apply_patch contract, parser, and content transformer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


APPLY_PATCH_GRAMMAR = r"""start: begin_patch update_hunk+ end_patch
begin_patch: "*** Begin Patch" LF
end_patch: "*** End Patch" LF?

update_hunk: "*** Update File: " filename LF change
filename: /(.+)/

change: (change_context | change_line)+ eof_line?
change_context: ("@@" | "@@ " /(.+)/) LF
change_line: ("+" | "-" | " ") /(.*)/ LF
eof_line: "*** End of File" LF

%import common.LF
"""
APPLY_PATCH_FORMAT = {
    "type": "grammar",
    "syntax": "lark",
    "definition": APPLY_PATCH_GRAMMAR,
}

FREEFORM_APPLY_PATCH_DESCRIPTION = (
    "Apply a patch to declared prompt files. This is a FREEFORM tool: pass the "
    "raw patch directly, without JSON, Markdown fences, or shell/heredoc syntax. "
    "Only Update File operations are allowed."
)

FUNCTION_APPLY_PATCH_DESCRIPTION = """Apply a patch to declared prompt files.

Pass one JSON object with exactly one field:
{"input": "<the complete raw patch text>"}

The input string must use this Update-only format:
*** Begin Patch
*** Update File: relative/path/to/prompt.md
@@ exact existing line copied from the prompt
 unchanged context line
-old line
+new line
 unchanged context line
*** End Patch

Rules:
- Use only `*** Update File`; never add, delete, or move files.
- Paths must be relative and must identify a declared prompt file.
- Start each normal hunk with `@@`. Any text after `@@ ` must be copied
  verbatim from one complete existing prompt line, including punctuation,
  Markdown markers, and quotes. Use bare `@@` when an exact anchor is not needed.
- Prefix unchanged, removed, and added lines with space, `-`, and `+`.
- These prefixes are separate from the file content. For example, replacing a
  Markdown bullet `- old rule` requires patch lines `-- old rule` and
  `+- new rule`; unchanged bullet context is written as ` - nearby rule`.
- Include enough unchanged context to identify the target, but not the full file.
- Put the patch directly in `input`; do not wrap it in Markdown fences or encode
  another JSON object inside the string.
"""

MINIMAL_PATCH_TEMPLATE = (
    "*** Begin Patch\n"
    "*** Update File: relative/prompt.md\n"
    "@@\n"
    "-old text\n"
    "+new text\n"
    "*** End Patch"
)


class PromptPatchInput(BaseModel):
    """JSON-function arguments for apply_patch."""

    model_config = ConfigDict(extra="forbid")

    input: str = Field(
        min_length=1,
        description=(
            "The complete raw Update-only apply_patch text, beginning with "
            "'*** Begin Patch' and ending with '*** End Patch'."
        ),
    )


class PromptPatchError(ValueError):
    """Actionable patch failure returned to the model."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        line: int | None = None,
        path: str | Path | None = None,
        expected: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.error_code = code
        self.line = line
        self.path = str(path) if path is not None else None
        self.expected = expected
        self.hint = hint
        parts = [f"[{code}] {message}"]
        if line is not None:
            parts.append(f"line: {line}")
        if self.path is not None:
            parts.append(f"path: {self.path}")
        if expected:
            parts.append(f"expected:\n{expected}")
        if hint:
            parts.append(f"hint: {hint}")
        super().__init__("\n".join(parts))


@dataclass(frozen=True)
class UpdateChunk:
    anchor: str | None
    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]
    end_of_file: bool


@dataclass(frozen=True)
class FileUpdate:
    path: str
    chunks: tuple[UpdateChunk, ...]


def parse_prompt_patch(patch: str) -> tuple[FileUpdate, ...]:
    """Parse the safe Update-only subset with Codex-compatible leniency."""

    text = str(patch).strip()
    if text.startswith("```"):
        raise PromptPatchError(
            "markdown_wrapper",
            "Markdown fences are not valid apply_patch input.",
            expected=MINIMAL_PATCH_TEMPLATE,
            hint="Send the raw patch text directly.",
        )

    lines = text.splitlines()
    if (
        len(lines) >= 4
        and lines[0] in {"<<EOF", "<<'EOF'", '<<"EOF"'}
        and lines[-1].endswith("EOF")
    ):
        lines = lines[1:-1]

    if not lines or lines[0].strip() != "*** Begin Patch":
        raise PromptPatchError(
            "missing_begin",
            "The first patch line must be '*** Begin Patch'.",
            line=1,
            expected=MINIMAL_PATCH_TEMPLATE,
        )
    if lines[-1].strip() != "*** End Patch":
        raise PromptPatchError(
            "missing_end",
            "The last patch line must be '*** End Patch'.",
            line=len(lines),
            expected=MINIMAL_PATCH_TEMPLATE,
        )

    updates: list[FileUpdate] = []
    index = 1
    last_index = len(lines) - 1
    while index < last_index:
        if not lines[index].strip():
            index += 1
            continue

        header = lines[index].strip()
        if header.startswith(
            ("*** Add File: ", "*** Delete File: ", "*** Move to: ")
        ):
            raise PromptPatchError(
                "unsupported_operation",
                "Prompt patches may only use '*** Update File'.",
                line=index + 1,
                expected="*** Update File: relative/path/to/prompt.md",
            )
        if not header.startswith("*** Update File: "):
            raise PromptPatchError(
                "invalid_file_header",
                "Expected an Update File header.",
                line=index + 1,
                expected="*** Update File: relative/path/to/prompt.md",
            )

        raw_path = header.removeprefix("*** Update File: ").strip()
        if not raw_path:
            raise PromptPatchError(
                "missing_path",
                "Update File path must not be empty.",
                line=index + 1,
            )
        if Path(raw_path).is_absolute():
            raise PromptPatchError(
                "absolute_path",
                "Prompt patch paths must be relative.",
                line=index + 1,
                path=raw_path,
                hint="Use the path shown by inspect_slave or read_prompt.",
            )

        index += 1
        chunks: list[UpdateChunk] = []
        while index < last_index:
            if lines[index].strip().startswith("*** Update File: "):
                break
            if lines[index].strip().startswith(
                ("*** Add File: ", "*** Delete File: ", "*** Move to: ")
            ):
                raise PromptPatchError(
                    "unsupported_operation",
                    "Prompt patches may only use '*** Update File'.",
                    line=index + 1,
                )
            if not lines[index] and _blank_separates_chunks(lines, index, last_index):
                index += 1
                continue

            allow_missing_context = not chunks
            chunk, index = _parse_chunk(
                lines,
                index,
                last_index,
                path=raw_path,
                allow_missing_context=allow_missing_context,
            )
            chunks.append(chunk)

        if not chunks:
            raise PromptPatchError(
                "empty_update",
                "Update File must contain at least one change hunk.",
                line=index + 1,
                path=raw_path,
                expected="@@\n-old text\n+new text",
            )
        updates.append(FileUpdate(path=raw_path, chunks=tuple(chunks)))

    if not updates:
        raise PromptPatchError(
            "empty_patch",
            "Patch must contain at least one Update File operation.",
            expected=MINIMAL_PATCH_TEMPLATE,
        )
    return tuple(updates)


def apply_file_update(content: str, update: FileUpdate, *, path: Path) -> str:
    """Compute updated contents without mutating the filesystem."""

    trailing_newline = content.endswith("\n")
    original = content.split("\n")
    if trailing_newline:
        original.pop()

    replacements: list[tuple[int, int, tuple[str, ...]]] = []
    line_index = 0
    for chunk in update.chunks:
        anchor_missing = False
        anchor_index: int | None = None
        if chunk.anchor is not None:
            anchor_index = _find_sequence(
                original,
                [chunk.anchor],
                start=line_index,
                end_of_file=False,
            )
            if anchor_index is None:
                anchor_missing = True
            else:
                line_index = anchor_index + 1

        old_lines = list(chunk.old_lines)
        new_lines = list(chunk.new_lines)
        if anchor_index is not None and _sequence_matches_at(
            original,
            old_lines,
            index=anchor_index,
        ):
            match_index = anchor_index
        elif anchor_missing:
            match_index = _find_unique_sequence(
                original,
                old_lines,
                start=line_index,
                end_of_file=chunk.end_of_file,
            )
        else:
            match_index = _find_sequence(
                original,
                old_lines,
                start=line_index,
                end_of_file=chunk.end_of_file,
            )
        if match_index is None and old_lines and old_lines[-1] == "":
            old_lines.pop()
            if new_lines and new_lines[-1] == "":
                new_lines.pop()
            if anchor_index is not None and _sequence_matches_at(
                original,
                old_lines,
                index=anchor_index,
            ):
                match_index = anchor_index
            else:
                finder = _find_unique_sequence if anchor_missing else _find_sequence
                match_index = finder(
                    original,
                    old_lines,
                    start=line_index,
                    end_of_file=chunk.end_of_file,
                )
        if anchor_missing and match_index is None:
            raise PromptPatchError(
                "context_anchor_not_found",
                "The @@ anchor was not an exact prompt line, and the hunk's old "
                "lines were not unique enough to apply safely.",
                path=path,
                expected=chunk.anchor,
                hint=(
                    "Copy the complete anchor line from read_prompt, including "
                    "punctuation and Markdown markers, or use bare @@ with unique "
                    "unchanged/removed lines."
                ),
            )
        if match_index is None:
            raise PromptPatchError(
                "context_not_found",
                "Could not find the expected hunk lines in the current prompt.",
                path=path,
                expected="\n".join(chunk.old_lines),
                hint="Call read_prompt and copy the exact current lines into the patch.",
            )

        match_end = match_index + len(old_lines)
        if chunk.end_of_file and match_end != len(original):
            raise PromptPatchError(
                "end_of_file_mismatch",
                "The End of File hunk does not reach the prompt's final line.",
                path=path,
                expected="\n".join(chunk.old_lines),
            )
        replacements.append((match_index, len(old_lines), tuple(new_lines)))
        line_index = match_end

    current = list(original)
    for start, old_length, new_lines in reversed(replacements):
        current[start : start + old_length] = new_lines

    result = "\n".join(current)
    if trailing_newline:
        result += "\n"
    return result


def _parse_chunk(
    lines: list[str],
    index: int,
    last_index: int,
    *,
    path: str,
    allow_missing_context: bool,
) -> tuple[UpdateChunk, int]:
    start_line = index + 1
    line = lines[index]
    if line == "@@":
        anchor = None
        index += 1
    elif line.startswith("@@ "):
        anchor = line[3:]
        index += 1
    elif allow_missing_context and _is_change_line(line):
        anchor = None
    else:
        raise PromptPatchError(
            "missing_hunk_header",
            "Each change hunk must start with '@@'.",
            line=start_line,
            path=path,
            expected="@@\n-old text\n+new text",
        )

    old_lines: list[str] = []
    new_lines: list[str] = []
    parsed_change = False
    end_of_file = False
    while index < last_index:
        line = lines[index]
        stripped = line.strip()
        if line.startswith("@@") or stripped.startswith("*** Update File: "):
            break
        if not line and _blank_separates_chunks(lines, index, last_index):
            break
        if stripped.startswith(
            ("*** Add File: ", "*** Delete File: ", "*** Move to: ")
        ):
            break
        if line == "*** End of File":
            if not parsed_change:
                raise PromptPatchError(
                    "empty_hunk",
                    "End of File cannot appear before hunk lines.",
                    line=index + 1,
                    path=path,
                )
            end_of_file = True
            index += 1
            break
        if not line:
            old_lines.append("")
            new_lines.append("")
        elif line[0] == " ":
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line[0] == "-":
            old_lines.append(line[1:])
        elif line[0] == "+":
            new_lines.append(line[1:])
        else:
            raise PromptPatchError(
                "invalid_change_line",
                "Hunk lines must start with space, '-', or '+'.",
                line=index + 1,
                path=path,
                expected=" unchanged context\n-old text\n+new text",
            )
        parsed_change = True
        index += 1

    if not parsed_change:
        raise PromptPatchError(
            "empty_hunk",
            "Change hunk does not contain any context or changed lines.",
            line=start_line,
            path=path,
        )
    return (
        UpdateChunk(
            anchor=anchor,
            old_lines=tuple(old_lines),
            new_lines=tuple(new_lines),
            end_of_file=end_of_file,
        ),
        index,
    )


def _blank_separates_chunks(lines: list[str], index: int, last_index: int) -> bool:
    next_index = index + 1
    while next_index < last_index and not lines[next_index]:
        next_index += 1
    return next_index < last_index and (
        lines[next_index].startswith("@@")
        or lines[next_index].strip().startswith("*** Update File: ")
    )


def _is_change_line(line: str) -> bool:
    return not line or line.startswith((" ", "+", "-"))


def _find_sequence(
    lines: list[str],
    target: list[str],
    *,
    start: int,
    end_of_file: bool,
) -> int | None:
    matches = _find_sequence_matches(
        lines,
        target,
        start=start,
        end_of_file=end_of_file,
    )
    return matches[0] if matches else None


def _find_unique_sequence(
    lines: list[str],
    target: list[str],
    *,
    start: int,
    end_of_file: bool,
) -> int | None:
    matches = _find_sequence_matches(
        lines,
        target,
        start=start,
        end_of_file=end_of_file,
    )
    return matches[0] if len(matches) == 1 else None


def _find_sequence_matches(
    lines: list[str],
    target: list[str],
    *,
    start: int,
    end_of_file: bool,
) -> list[int]:
    if not target:
        return []
    if len(target) > len(lines):
        return []

    last_start = len(lines) - len(target)
    search_start = last_start if end_of_file else start
    candidate_indexes = range(search_start, last_start + 1)
    normalizers: tuple[Any, ...] = (
        lambda value: value,
        lambda value: value.rstrip(),
        lambda value: value.strip(),
        _normalize_context,
    )
    for normalize in normalizers:
        normalized_target = [normalize(line) for line in target]
        matches: list[int] = []
        for candidate_index in candidate_indexes:
            candidate = lines[candidate_index : candidate_index + len(target)]
            if [normalize(line) for line in candidate] == normalized_target:
                matches.append(candidate_index)
        if matches:
            return matches
    return []


def _sequence_matches_at(
    lines: list[str],
    target: list[str],
    *,
    index: int,
) -> bool:
    if not target or index + len(target) > len(lines):
        return False
    candidate = lines[index : index + len(target)]
    normalizers: tuple[Any, ...] = (
        lambda value: value,
        lambda value: value.rstrip(),
        lambda value: value.strip(),
        _normalize_context,
    )
    return any(
        [normalize(line) for line in candidate]
        == [normalize(line) for line in target]
        for normalize in normalizers
    )


_CONTEXT_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00a0": " ",
        "\u2002": " ",
        "\u2003": " ",
        "\u2004": " ",
        "\u2005": " ",
        "\u2006": " ",
        "\u2007": " ",
        "\u2008": " ",
        "\u2009": " ",
        "\u200a": " ",
        "\u202f": " ",
        "\u205f": " ",
        "\u3000": " ",
    }
)


def _normalize_context(value: str) -> str:
    return value.strip().translate(_CONTEXT_TRANSLATION)
