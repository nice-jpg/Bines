"""Wrapper around the trusted nice_dumper Android command."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


class DumpError(RuntimeError):
    """Raised when the trusted dumper cannot produce valid XML."""


@dataclass(frozen=True)
class DumperConfig:
    adb: str = "adb"
    remote_output: str = "/sdcard/nice-dumper-agent/full.xml"
    timeout_ms: int = 15000
    fixture_xml: Path | None = None


def dump_full_xml(config: DumperConfig) -> str:
    """Capture full XML with the installed /data/local/tmp/project dumper."""

    if config.fixture_xml:
        return _validate_xml(config.fixture_xml.read_text(encoding="utf-8"))

    remote_dir = config.remote_output.rsplit("/", 1)[0] or "/sdcard"
    mkdir_cmd = f"mkdir -p {shlex.quote(remote_dir)}"
    dump_cmd = (
        f"/data/local/tmp/project -d {shlex.quote(config.remote_output)} "
        f"--timeout-ms {int(config.timeout_ms)}"
    )
    _run_adb_shell(config.adb, mkdir_cmd)
    _run_adb_shell(config.adb, f"su -c {shlex.quote(dump_cmd)}")
    raw_xml = _run_adb_shell(config.adb, f"su -c {shlex.quote('cat ' + shlex.quote(config.remote_output))}")
    return _validate_xml(raw_xml)


def _run_adb_shell(adb: str, shell_command: str) -> str:
    args = [adb, "shell", shell_command]
    completed = subprocess.run(args, check=False, text=True, capture_output=True, encoding="utf-8")
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout or "").strip()
        raise DumpError(output or f"adb command failed: {' '.join(args)}")
    return completed.stdout


def _validate_xml(raw: str) -> str:
    text = str(raw or "")
    start_candidates = [idx for idx in (text.find("<?xml"), text.find("<hierarchy")) if idx >= 0]
    if not start_candidates:
        raise DumpError("dump output does not contain <hierarchy")
    xml = text[min(start_candidates) :]
    end = xml.find("</hierarchy>")
    if end < 0:
        raise DumpError("dump output does not contain </hierarchy>")
    return xml[: end + len("</hierarchy>")].strip()
