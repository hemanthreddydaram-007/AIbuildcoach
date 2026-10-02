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
