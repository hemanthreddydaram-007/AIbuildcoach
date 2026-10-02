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


class GitIgnoreRule:
    def __init__(self, pattern: str, base_dir: Path, is_negation: bool = False, directory_only: bool = False):
        self.raw_pattern = pattern
        self.base_dir = base_dir.resolve()
        self.is_negation = is_negation
        self.directory_only = directory_only
        self._regex = self._compile_pattern(pattern)

    def _compile_pattern(self, pattern: str) -> re.Pattern:
        # Normalize pattern
        p = pattern.rstrip("/")
        # Convert gitignore glob pattern to regex
        # Handle **/ and /** and *
        parts = p.split("/")
        regex_parts = []
        for i, part in enumerate(parts):
            if part == "**":
                regex_parts.append(".*")
            elif "**" in part:
                # e.g. a**b
                subparts = part.split("**")
                escaped = [fnmatch.translate(sp)[4:-3] for sp in subparts]
                regex_parts.append(".*".join(escaped))
            else:
                tr = fnmatch.translate(part)
                # fnmatch.translate wraps with (?s:...) and \Z, extract the core regex
                if tr.startswith("(?s:") and tr.endswith(")\\Z"):
                    core = tr[4:-3]
                elif tr.endswith("\\Z"):
                    core = tr[:-2]
                else:
                    core = tr
                regex_parts.append(core)

        if "/" in p:
            # Pattern has slash, matches relative to base_dir
            compiled_regex = "^" + "/".join(regex_parts) + "(?:/.*)?$"
        else:
            # Matches in any directory level under base_dir
            compiled_regex = "(?:^|.*/)" + regex_parts[0] + "(?:/.*)?$"

        return re.compile(compiled_regex, re.IGNORECASE)

    def matches(self, rel_path: str, is_dir: bool) -> bool:
        if self.directory_only and not is_dir:
            return False
        # Normalize rel_path with forward slashes
        clean_path = rel_path.replace("\\", "/").strip("/")
        return bool(self._regex.search(clean_path))


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
