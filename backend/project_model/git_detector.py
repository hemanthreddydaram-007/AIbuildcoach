"""Safe, read-only Git state detector."""

import subprocess
import shutil
from pathlib import Path
from typing import Optional

from backend.domain.models import GitState


def detect_git_state(project_root: Path) -> GitState:
    """Safely inspects git status for project_root using read-only commands.
    
    If git is missing or project_root is not a git repo, returns a default GitState(is_git_repo=False).
    Never mutates git state.
    """
    root = project_root.resolve()
    git_dir = root / ".git"
    if not git_dir.exists():
        return GitState(is_git_repo=False)

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

    # Verify repo
    is_worktree = _run_git(["rev-parse", "--is-inside-work-tree"])
    if is_worktree != "true":
        return GitState(is_git_repo=False)

    # Current branch
    current_branch = _run_git(["branch", "--show-current"])
    if not current_branch:
        # Fallback for detached HEAD
        current_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])

    # HEAD commit
    head_commit = _run_git(["rev-parse", "HEAD"])

    # Git status --porcelain
    status_output = _run_git(["status", "--porcelain"])
    untracked_count = 0
    modified_count = 0
    staged_count = 0
    is_dirty = False

    if status_output is not None:
        lines = [line for line in status_output.splitlines() if line.strip()]
        if lines:
            is_dirty = True
        for line in lines:
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
        current_branch=current_branch if current_branch else None,
        head_commit=head_commit if head_commit else None,
        is_dirty=is_dirty,
        untracked_count=untracked_count,
        modified_count=modified_count,
        staged_count=staged_count,
    )
