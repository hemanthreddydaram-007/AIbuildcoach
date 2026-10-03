"""Deterministic unified diff parser for read-only Git inspection."""

import re
from typing import List, Optional, Tuple


HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def _unquote_git_path(p: str) -> str:
    """Strips git diff quotes and prefix indicators safely."""
    p = p.strip()
    if p.startswith('"') and p.endswith('"'):
        p = p[1:-1]
        try:
            p = p.encode("latin-1").decode("unicode_escape", errors="replace")
        except Exception:
            pass
    if p.startswith("a/") or p.startswith("b/"):
        return p[2:]
    return p


class ParsedHunk:
    def __init__(
        self,
        old_start: int,
        old_lines: int,
        new_start: int,
        new_lines: int,
        header: Optional[str],
        content: str,
    ):
        self.old_start = old_start
        self.old_lines = old_lines
        self.new_start = new_start
        self.new_lines = new_lines
        self.header = header.strip() if header else None
        self.content = content


class ParsedFileDiff:
    def __init__(
        self,
        old_path: Optional[str],
        new_path: str,
        change_type: str,
        is_binary: bool = False,
        hunks: Optional[List[ParsedHunk]] = None,
        old_line_count: Optional[int] = None,
        new_line_count: Optional[int] = None,
    ):
        self.old_path = old_path
        self.new_path = new_path
        self.change_type = change_type
        self.is_binary = is_binary
        self.hunks = hunks or []
        self.old_line_count = old_line_count
        self.new_line_count = new_line_count


def parse_unified_diff(diff_text: str) -> List[ParsedFileDiff]:
    """Parses standard Git unified diff output into structured file diffs and hunks.
    
    Treats diff input as untrusted; never evaluates or executes code.
    """
    if not diff_text or not diff_text.strip():
        return []

    lines = diff_text.splitlines()
    file_diffs: List[ParsedFileDiff] = []

    current_old: Optional[str] = None
    current_new: Optional[str] = None
    current_type = "MODIFIED"
    current_is_binary = False
    current_hunks: List[ParsedHunk] = []

    current_hunk_old_start = 0
    current_hunk_old_lines = 0
    current_hunk_new_start = 0
    current_hunk_new_lines = 0
    current_hunk_header: Optional[str] = None
    current_hunk_lines: List[str] = []
    in_hunk = False

    def _flush_hunk():
        nonlocal in_hunk, current_hunk_lines
        if in_hunk and (current_hunk_lines or current_hunk_old_lines or current_hunk_new_lines):
            current_hunks.append(
                ParsedHunk(
                    old_start=current_hunk_old_start,
                    old_lines=current_hunk_old_lines,
                    new_start=current_hunk_new_start,
                    new_lines=current_hunk_new_lines,
                    header=current_hunk_header,
                    content="\n".join(current_hunk_lines),
                )
            )
        in_hunk = False
        current_hunk_lines = []

    def _flush_file():
        nonlocal current_old, current_new, current_type, current_is_binary, current_hunks
        _flush_hunk()
        if current_new:
            deleted_lines = sum(
                sum(1 for l in h.content.splitlines() if l.startswith("-"))
                for h in current_hunks
            )
            added_lines = sum(
                sum(1 for l in h.content.splitlines() if l.startswith("+"))
                for h in current_hunks
            )
            file_diffs.append(
                ParsedFileDiff(
                    old_path=current_old if (current_old != current_new or current_type == "RENAMED") else current_old,
                    new_path=current_new,
                    change_type=current_type,
                    is_binary=current_is_binary,
                    hunks=list(current_hunks),
                    old_line_count=deleted_lines if deleted_lines > 0 else None,
                    new_line_count=added_lines if added_lines > 0 else None,
                )
            )
        current_old = None
        current_new = None
        current_type = "MODIFIED"
        current_is_binary = False
        current_hunks = []

    for line in lines:
        if line.startswith("diff --git "):
            _flush_file()
            # Parse diff --git a/path b/path with space/quote handling
            after_prefix = line[len("diff --git "):]
            if line.count('"') >= 4:
                quotes = re.findall(r'"([^"]+)"', after_prefix)
                if len(quotes) >= 2:
                    current_old = _unquote_git_path(quotes[0])
                    current_new = _unquote_git_path(quotes[1])
                else:
                    parts = after_prefix.split(" ")
                    if len(parts) >= 2:
                        current_old = _unquote_git_path(parts[0])
                        current_new = _unquote_git_path(parts[1])
            else:
                parts = after_prefix.split(" ")
                if len(parts) >= 2:
                    current_old = _unquote_git_path(parts[0])
                    current_new = _unquote_git_path(parts[1])
            continue

        if line.startswith("new file mode "):
            current_type = "ADDED"
            continue
        if line.startswith("deleted file mode "):
            current_type = "DELETED"
            continue
        if line.startswith("rename from "):
            current_old = _unquote_git_path(line[len("rename from "):])
            current_type = "RENAMED"
            continue
        if line.startswith("rename to "):
            current_new = _unquote_git_path(line[len("rename to "):])
            current_type = "RENAMED"
            continue
        if "Binary files " in line or "GIT binary patch" in line:
            current_is_binary = True
            continue

        if line.startswith("--- "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                current_type = "ADDED"
            continue
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                current_type = "DELETED"
            elif not current_new:
                current_new = _unquote_git_path(raw)
            continue

        hunk_match = HUNK_HEADER_RE.match(line)
        if hunk_match:
            _flush_hunk()
            in_hunk = True
            current_hunk_old_start = int(hunk_match.group(1))
            current_hunk_old_lines = int(hunk_match.group(2)) if hunk_match.group(2) is not None else 1
            current_hunk_new_start = int(hunk_match.group(3))
            current_hunk_new_lines = int(hunk_match.group(4)) if hunk_match.group(4) is not None else 1
            current_hunk_header = hunk_match.group(5).strip() if hunk_match.group(5) else None
            current_hunk_lines = [line]
            continue

        if in_hunk:
            current_hunk_lines.append(line)

    _flush_file()
    return file_diffs
