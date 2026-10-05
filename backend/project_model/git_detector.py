"""Safe, read-only Git state detector."""

import subprocess
import shutil
from pathlib import Path
from typing import Optional

from backend.domain.models import GitState


def detect_git_state(project_root: Path, git_bin: Optional[str] = None) -> GitState:
    """Safely inspects git status for project_root using read-only commands.
    
    If git is missing or project_root is not a git repo, returns a default GitState(is_git_repo=False).
    Never mutates git state.
    """
    root = project_root.resolve()
    git_dir = root / ".git"
    if not git_dir.exists():
        return GitState(is_git_repo=False)

    if not git_bin:
        git_bin = shutil.which("git")
    if not git_bin:
        return GitState(is_git_repo=False)

    def _run_git(args: list[str]) -> Optional[str]:
        try:
            res = subprocess.run(
                [git_bin] + args,
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0:
                return res.stdout.strip()
            return None
        except (subprocess.SubprocessError, OSError):
            return None

    # Consolidated Command 1: status with branch info
    status_output = _run_git(["status", "--porcelain=v1", "-b"])
    if status_output is None:
        return GitState(is_git_repo=False)

    lines = status_output.splitlines()
    branch: Optional[str] = None
    if lines and lines[0].startswith("## "):
        header = lines[0][3:].strip()
        if header.startswith("No commits yet on "):
            branch = header[len("No commits yet on "):].strip()
        elif header.startswith("Initial commit on "):
            branch = header[len("Initial commit on "):].strip()
        elif header.startswith("HEAD (no branch)"):
            branch = "HEAD"
        else:
            branch = header.split("...")[0].split(" ")[0]
        if not branch:
            branch = None

    # Consolidated Command 2: HEAD commit hash (if any commits exist)
    head_commit = _run_git(["rev-parse", "-q", "--verify", "HEAD"])

    untracked_count = 0
    modified_count = 0
    staged_count = 0
    is_dirty = False

    # Remaining lines contain changed files
    file_lines = lines[1:] if len(lines) > 1 else []
    if file_lines:
        is_dirty = True
        for line in file_lines:
            if len(line) < 2:
                continue
            index_status = line[0]
            worktree_status = line[1]

            if index_status == "?" and worktree_status == "?":
                untracked_count += 1
            else:
                if index_status in ("M", "A", "D", "R", "C"):
                    staged_count += 1
                if worktree_status in ("M", "D"):
                    modified_count += 1

    return GitState(
        is_git_repo=True,
        current_branch=branch,
        head_commit=head_commit if head_commit else None,
        is_dirty=is_dirty,
        untracked_count=untracked_count,
        modified_count=modified_count,
        staged_count=staged_count,
    )
