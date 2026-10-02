"""Tests for safe Git state detection."""

import subprocess
from pathlib import Path
from backend.project_model.git_detector import detect_git_state


def test_non_git_directory(tmp_path: Path):
    state = detect_git_state(tmp_path)
    assert state.is_git_repo is False
    assert state.current_branch is None
    assert state.head_commit is None
    assert state.is_dirty is False


def test_git_repo_state(tmp_path: Path):
    # Initialize a temporary git repo
    subprocess.run(["git", "init"], cwd=str(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    
    # State before any commit
    state_empty = detect_git_state(tmp_path)
    assert state_empty.is_git_repo is True
    assert state_empty.head_commit is None
    
    # Create a file
    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello git", encoding="utf-8")
    
    state_untracked = detect_git_state(tmp_path)
    assert state_untracked.is_dirty is True
    assert state_untracked.untracked_count == 1
    
    # Commit the file
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "add", "hello.txt"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), check=True)
    
    state_committed = detect_git_state(tmp_path)
    assert state_committed.is_git_repo is True
    assert state_committed.head_commit is not None
    assert state_committed.is_dirty is False
    assert state_committed.untracked_count == 0
    assert state_committed.modified_count == 0


def test_git_worktree_file_pointer(tmp_path: Path):
    main_repo = (tmp_path / "main_repo").resolve()
    main_repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(main_repo), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(main_repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=str(main_repo), check=True)
    (main_repo / "main.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "main.txt"], cwd=str(main_repo), check=True)
    subprocess.run(["git", "commit", "-m", "init commit"], cwd=str(main_repo), check=True, stdout=subprocess.PIPE)

    # Create worktree
    wt_dir = (tmp_path / "worktree_repo").resolve()
    subprocess.run(
        ["git", "worktree", "add", str(wt_dir), "-b", "feature-worktree"],
        cwd=str(main_repo),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Verify that in wt_dir, .git is a pointer file rather than a directory
    git_pointer = wt_dir / ".git"
    assert git_pointer.is_file()

    # detect_git_state should recognize worktree and read branch and commit
    state = detect_git_state(wt_dir)
    assert state.is_git_repo is True
    assert state.current_branch == "feature-worktree"
    assert state.head_commit is not None
    assert state.is_dirty is False
