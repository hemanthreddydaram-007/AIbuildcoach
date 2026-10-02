"""Gitignore and exclusion pattern parser and matcher."""

import re
import fnmatch
from pathlib import Path
from typing import List, Tuple, Optional

DEFAULT_EXCLUSIONS = [
    ".git",
    "node_modules",
    "dist",
    "build",
    ".next",
    "coverage",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".cache",
    "generated",
    "tmp",
    ".buildcoach",
]


def _translate_segment(segment: str) -> str:
    """Converts a single glob segment into regex matching within that segment (no slashes)."""
    i = 0
    n = len(segment)
    res = []
    while i < n:
        c = segment[i]
        if c == "*":
            res.append("[^/]*")
            i += 1
        elif c == "?":
            res.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and segment[j] in ("!", "^"):
                j += 1
            if j < n and segment[j] == "]":
                j += 1
            while j < n and segment[j] != "]":
                j += 1
            if j >= n:
                res.append(r"\[")
                i += 1
            else:
                stuff = segment[i + 1 : j]
                i = j + 1
                if stuff.startswith(("!", "^")):
                    stuff = "^" + stuff[1:]
                res.append(f"[{stuff}]")
        else:
            res.append(re.escape(c))
            i += 1
    return "".join(res)


def _parts_to_regex(parts: List[str]) -> str:
    """Combines path segments into a path regex handling ** and single *."""
    if "**" not in parts:
        return "/".join(_translate_segment(p) for p in parts)

    segments = []
    for i, p in enumerate(parts):
        if p == "**":
            if i == 0 and i == len(parts) - 1:
                segments.append(".*")
            elif i == 0:
                segments.append("(?:.*/)?")
            elif i == len(parts) - 1:
                segments.append("/.*")
            else:
                segments.append("/(?:.+/)?")
        else:
            if i > 0 and parts[i - 1] != "**":
                segments.append("/")
            segments.append(_translate_segment(p))
    return "".join(segments)


class GitIgnoreRule:
    def __init__(self, pattern: str, base_dir: Path, is_negation: bool = False, directory_only: bool = False):
        self.raw_pattern = pattern
        self.base_dir = base_dir.resolve()
        self.is_negation = is_negation
        self.directory_only = directory_only
        self.is_root_anchored = False
        self._exact_regex, self._child_regex = self._compile_pattern(pattern)

    def _compile_pattern(self, pattern: str) -> Tuple[re.Pattern, re.Pattern]:
        p = pattern.rstrip("/")
        if p.startswith("/"):
            self.is_root_anchored = True
            clean_pattern = p.lstrip("/")
        elif "/" in p:
            self.is_root_anchored = True
            clean_pattern = p
        else:
            self.is_root_anchored = False
            clean_pattern = p

        parts = clean_pattern.split("/")
        body = _parts_to_regex(parts)

        if self.is_root_anchored:
            exact_p = f"^{body}$"
            child_p = f"^{body}/.*$"
        else:
            exact_p = f"(?:^|.*/){body}$"
            child_p = f"(?:^|.*/){body}/.*$"

        return re.compile(exact_p, re.IGNORECASE), re.compile(child_p, re.IGNORECASE)

    def matches(self, rel_path: str, is_dir: bool) -> bool:
        clean_path = rel_path.replace("\\", "/").strip("/")
        if not clean_path:
            return False

        if self.directory_only:
            if is_dir:
                return bool(self._exact_regex.search(clean_path) or self._child_regex.search(clean_path))
            else:
                return bool(self._child_regex.search(clean_path))
        else:
            return bool(self._exact_regex.search(clean_path) or self._child_regex.search(clean_path))


class IgnoreFilter:
    def __init__(self, root_dir: Path, custom_exclusions: Optional[List[str]] = None):
        self.root_dir = root_dir.resolve()
        self.rules: List[GitIgnoreRule] = []
        self._init_default_exclusions(custom_exclusions)
        self.load_gitignore(self.root_dir / ".gitignore")

    def _init_default_exclusions(self, custom: Optional[List[str]] = None) -> None:
        exclusions = list(DEFAULT_EXCLUSIONS)
        if custom:
            exclusions.extend(custom)
        for excl in exclusions:
            self.rules.append(GitIgnoreRule(excl, self.root_dir, is_negation=False, directory_only=False))

    def load_gitignore(self, gitignore_path: Path) -> None:
        if not gitignore_path.is_file():
            return
        base_dir = gitignore_path.parent.resolve()
        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    is_negation = False
                    if line.startswith("!"):
                        is_negation = True
                        line = line[1:].strip()
                    if not line:
                        continue
                    dir_only = line.endswith("/")
                    pattern = line.rstrip("/")
                    self.rules.append(GitIgnoreRule(pattern, base_dir, is_negation=is_negation, directory_only=dir_only))
        except (IOError, OSError):
            pass

    def is_ignored(self, path: Path, is_dir: bool = False) -> bool:
        try:
            resolved = path.resolve()
            rel_path = str(resolved.relative_to(self.root_dir)).replace("\\", "/")
        except ValueError:
            # Path outside root
            return True

        if rel_path == "." or not rel_path:
            return False

        # Check default exclusions by path segments
        parts = rel_path.split("/")
        for part in parts:
            if part in DEFAULT_EXCLUSIONS:
                return True

        # If checking a file or sub-directory, verify if any parent directory is ignored
        if not is_dir or "/" in rel_path:
            parent = resolved.parent
            if parent != resolved and parent != self.root_dir and self.root_dir in parent.parents:
                if self.is_ignored(parent, is_dir=True):
                    return True

        ignored = False
        for rule in self.rules:
            try:
                rule_rel = str(resolved.relative_to(rule.base_dir)).replace("\\", "/")
            except ValueError:
                continue

            if rule.matches(rule_rel, is_dir):
                if rule.is_negation:
                    ignored = False
                else:
                    ignored = True

        return ignored
