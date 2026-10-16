"""Grammar accepted by the collector patch tool."""

from typing import Final

RUN_AGENT_PY_PATCH_GRAMMAR: Final[str] = """start: begin_patch update_hunk+ end_patch
begin_patch: "*** Begin Patch" LF
end_patch: "*** End Patch" LF?

update_hunk: "*** Update File: " filename LF change
filename: /(.+)/

change: (change_context | change_line)+ eof_line?
change_context: ("@@" | "@@ " /(.+)/) LF
change_line: ("+" | "-" | " ") /(.*)/ LF
eof_line: "*** End of File" LF

%import common.LF"""
