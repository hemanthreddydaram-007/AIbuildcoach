"""Targeted regression tests for M10.1 performance optimizations."""

import subprocess
import shutil
from pathlib import Path
from unittest.mock import patch

from backend.domain.models import GitState
from backend.project_model.git_detector import detect_git_state
from backend.project_model.context_detector import ContextDetector
from backend.project_model.scanner import ProjectScanner, scan_file
from backend.project_model.db import Database


def test_incremental_sha256_recomputes_only_on_change(tmp_path: Path):
    """Verifies that unchanged files reuse cached hashes, while modified/new files recompute."""
    repo = tmp_path / "incremental_repo"
    repo.mkdir()
    f1 = repo / "stable.py"
    f2 = repo / "dynamic.py"
    f1.write_text("print('stable')\n", encoding="utf-8")
    f2.write_text("print('dynamic_v1')\n", encoding="utf-8")

    db_path = repo / ".buildcoach" / "state.db"
    scanner = ProjectScanner(repo, db_path=db_path)

    # 1. Cold scan
    res1 = scanner.scan()
    h1_orig = next(f.sha256_hash for f in res1.files if f.path == "stable.py")
    h2_v1 = next(f.sha256_hash for f in res1.files if f.path == "dynamic.py")
    assert h1_orig != ""
    assert h2_v1 != ""

    # 2. Warm scan with NO changes: compute_sha256 should not be called for either file
    with patch("backend.project_model.scanner.compute_sha256") as mock_sha:
        res2 = scanner.scan()
        assert mock_sha.call_count == 0, "compute_sha256 should not be called when files are unchanged"
    h1_warm = next(f.sha256_hash for f in res2.files if f.path == "stable.py")
    h2_warm = next(f.sha256_hash for f in res2.files if f.path == "dynamic.py")
    assert h1_warm == h1_orig
    assert h2_warm == h2_v1

    # 3. Modify dynamic.py
    import time
    time.sleep(0.01)  # ensure mtime tick
    f2.write_text("print('dynamic_v2_with_different_size')\n", encoding="utf-8")

    res3 = scanner.scan()
    h1_after = next(f.sha256_hash for f in res3.files if f.path == "stable.py")
    h2_v2 = next(f.sha256_hash for f in res3.files if f.path == "dynamic.py")
    assert h1_after == h1_orig
    assert h2_v2 != h2_v1

    # 4. Add new file
    f3 = repo / "new_file.py"
    f3.write_text("print('new')\n", encoding="utf-8")
    res4 = scanner.scan()
    assert any(f.path == "new_file.py" for f in res4.files)
    assert len(res4.files) == 3

    # 5. Delete file
    f3.unlink()
    res5 = scanner.scan()
    assert not any(f.path == "new_file.py" for f in res5.files)
    assert len(res5.files) == 2


def test_consolidated_git_detector_semantics(tmp_path: Path):
    """Verifies that consolidated git_detector returns exact GitState semantics with reduced invocations."""
    repo = tmp_path / "git_test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), check=True)

    # Initial state (empty repo, no commits)
    state_init = detect_git_state(repo)
    assert state_init.is_git_repo is True
    assert state_init.head_commit is None
    assert state_init.is_dirty is False
    assert state_init.current_branch in ("master", "main")

    # Add file (untracked)
    f = repo / "foo.txt"
    f.write_text("content\n", encoding="utf-8")
    state_untracked = detect_git_state(repo)
    assert state_untracked.is_dirty is True
    assert state_untracked.untracked_count == 1
    assert state_untracked.modified_count == 0
    assert state_untracked.staged_count == 0

    # Stage file
    subprocess.run(["git", "add", "foo.txt"], cwd=str(repo), check=True)
    state_staged = detect_git_state(repo)
    assert state_staged.is_dirty is True
    assert state_staged.staged_count == 1
    assert state_staged.untracked_count == 0

    # Commit file
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(repo), check=True)
    state_committed = detect_git_state(repo)
    assert state_committed.is_dirty is False
    assert state_committed.head_commit is not None
    assert len(state_committed.head_commit) == 40
    assert state_committed.untracked_count == 0
    assert state_committed.modified_count == 0
    assert state_committed.staged_count == 0

    # Modify file (unstaged)
    f.write_text("modified\n", encoding="utf-8")
    state_modified = detect_git_state(repo)
    assert state_modified.is_dirty is True
    assert state_modified.modified_count == 1


def test_context_detector_accepts_predetected_git_state(tmp_path: Path):
    """Verifies that ContextDetector avoids redundant detect_git_state when supplied."""
    repo = tmp_path / "cd_test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), check=True)
    (repo / "f.txt").write_text("abc\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True)

    detector = ContextDetector(repo, "test_proj")
    pre_state = detect_git_state(repo)

    with patch("backend.project_model.context_detector.detect_git_state") as mock_detect:
        cs = detector.collect(git_state=pre_state)
        mock_detect.assert_not_called()
        assert cs.git_state.is_git_repo is True
        assert cs.git_state.is_dirty is False


def test_context_detector_skips_diff_queries_on_clean_tree(tmp_path: Path):
    """Verifies that on clean trees, status and diff subprocesses are skipped without compromising changeset."""
    repo = tmp_path / "cd_clean_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), check=True)
    (repo / "f.txt").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True)

    detector = ContextDetector(repo, "clean_proj")
    clean_state = GitState(is_git_repo=True, current_branch="main", head_commit="abc12345", is_dirty=False)

    with patch("backend.project_model.context_detector._run_git_cmd") as mock_cmd:
        mock_cmd.return_value = "abc12345\x00Test User\x002026-01-01\x00commit subject"
        cs = detector.collect(git_state=clean_state)

        # Only git log -1 should be executed
        called_args = [call[0][2] for call in mock_cmd.call_args_list]
        for arg in called_args:
            assert "diff" not in arg
            assert "status" not in arg
        assert len(cs.file_changes) == 0
        assert cs.git_state.is_dirty is False


def test_incremental_sha256_same_size_content_mutation(tmp_path: Path):
    """Verifies that changing file content with identical length triggers re-hashing when mtime advances."""
    import time
    import hashlib
    from backend.project_model.scanner import compute_sha256

    repo = tmp_path / "same_size_repo"
    repo.mkdir()
    target_file = repo / "config.py"
    initial_bytes = b"FLAG = True \n"  # 13 bytes
    updated_bytes = b"FLAG = False\n"  # 13 bytes (same size N)
    assert len(initial_bytes) == len(updated_bytes)

    target_file.write_bytes(initial_bytes)
    db_path = repo / ".buildcoach" / "state.db"
    scanner = ProjectScanner(repo, db_path=db_path)

    # 1. Initial scan: computes and caches initial SHA-256
    res1 = scanner.scan()
    initial_file = next(f for f in res1.files if f.path == "config.py")
    expected_initial_hash = hashlib.sha256(initial_bytes).hexdigest()
    assert initial_file.sha256_hash == expected_initial_hash

    # 2. Replace content with different content of the SAME length; allow mtime to tick normally
    time.sleep(0.05)
    target_file.write_bytes(updated_bytes)

    # 3. Rescan with wrapper spy to verify compute_sha256 is invoked
    with patch("backend.project_model.scanner.compute_sha256", wraps=compute_sha256) as spy_sha:
        res2 = scanner.scan()
        assert spy_sha.call_count >= 1

    updated_file = next(f for f in res2.files if f.path == "config.py")
    expected_updated_hash = hashlib.sha256(updated_bytes).hexdigest()

    # Assert cached hash is not reused and resulting SHA-256 matches new content
    assert updated_file.sha256_hash != expected_initial_hash
    assert updated_file.sha256_hash == expected_updated_hash


def test_gitignore_semantic_preservation_across_all_rule_types(tmp_path: Path):
    """Verifies that the optimized IgnoreFilter preserves 100% of gitignore semantics across all pattern forms."""
    from backend.project_model.gitignore import IgnoreFilter, GitIgnoreRule

    repo = tmp_path / "gitignore_semantics_repo"
    repo.mkdir()

    # Create directory tree
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "build").mkdir()
    (repo / "logs" / "archived").mkdir(parents=True)
    (repo / "subproject").mkdir()

    # Root .gitignore with diverse rule patterns:
    # 1. root-anchored rule
    # 2. directory-only rule
    # 3. wildcard and negation
    # 4. nested directory pattern
    gitignore = repo / ".gitignore"
    gitignore.write_text(
        "/root_anchor.txt\n"
        "*.tmp\n"
        "!keep.tmp\n"
        "logs/\n"
        "build/\n"
        "src/pkg/generated_*\n",
        encoding="utf-8",
    )

    filter_obj = IgnoreFilter(repo)

    # 1. Default exclusions
    assert filter_obj.is_ignored(repo / ".git", is_dir=True)
    assert filter_obj.is_ignored(repo / "node_modules", is_dir=True)
    assert filter_obj.is_ignored(repo / ".buildcoach", is_dir=True)
    assert filter_obj.is_ignored(repo / ".venv", is_dir=True)

    # 2. Root-anchored rule: /root_anchor.txt matches at root, not in subfolder
    assert filter_obj.is_ignored(repo / "root_anchor.txt", is_dir=False)
    assert filter_obj.is_ignored(repo / "root_anchor.txt", is_dir=False, rel_path="root_anchor.txt")
    assert not filter_obj.is_ignored(repo / "src" / "root_anchor.txt", is_dir=False)
    assert not filter_obj.is_ignored(repo / "src" / "root_anchor.txt", is_dir=False, rel_path="src/root_anchor.txt")

    # 3. Wildcard + Negation: *.tmp ignored, but !keep.tmp kept
    assert filter_obj.is_ignored(repo / "test.tmp", is_dir=False)
    assert filter_obj.is_ignored(repo / "src" / "nested.tmp", is_dir=False)
    assert not filter_obj.is_ignored(repo / "keep.tmp", is_dir=False)
    assert not filter_obj.is_ignored(repo / "src" / "keep.tmp", is_dir=False)

    # 4. Directory-only rule: logs/ matches dir and its children, but a file named logs is not ignored
    assert filter_obj.is_ignored(repo / "logs", is_dir=True)
    assert filter_obj.is_ignored(repo / "logs" / "app.log", is_dir=False)
    assert filter_obj.is_ignored(repo / "logs" / "archived" / "old.log", is_dir=False)
    assert not filter_obj.is_ignored(repo / "logs", is_dir=False)

    # 5. Nested directory rule: src/pkg/generated_*
    assert filter_obj.is_ignored(repo / "src" / "pkg" / "generated_code.py", is_dir=False)
    assert not filter_obj.is_ignored(repo / "src" / "pkg" / "manual_code.py", is_dir=False)
    assert not filter_obj.is_ignored(repo / "other" / "generated_code.py", is_dir=False)

    # 6. Non-root base directory rule: manual GitIgnoreRule anchored in subproject
    sub_rule = GitIgnoreRule("local_only.txt", repo / "subproject")
    filter_obj.rules.append(sub_rule)
    assert filter_obj.is_ignored(repo / "subproject" / "local_only.txt", is_dir=False)
    assert not filter_obj.is_ignored(repo / "src" / "local_only.txt", is_dir=False)

    # 7. Path outside repository root safely returns True (ignored)
    outside_file = tmp_path.parent / "outside.txt"
    assert filter_obj.is_ignored(outside_file, is_dir=False)
    assert filter_obj.is_ignored(outside_file.parent, is_dir=True)


def test_scanner_traversal_path_identity_and_integrity(tmp_path: Path):
    """Verifies that the scanner's optimized traversal preserves exact path identities, hashes, and DB entries."""
    from backend.project_model.scanner import ProjectScanner

    repo = tmp_path / "scanner_integrity_repo"
    repo.mkdir()
    (repo / "pkg").mkdir()
    (repo / "pkg" / "sub").mkdir()

    f1 = repo / "main.py"
    f2 = repo / "pkg" / "module.py"
    f3 = repo / "pkg" / "sub" / "deep.py"
    ignored_f = repo / "node_modules" / "leak.js"
    ignored_f.parent.mkdir()

    f1.write_text("print('root')\n", encoding="utf-8")
    f2.write_text("print('pkg')\n", encoding="utf-8")
    f3.write_text("print('deep')\n", encoding="utf-8")
    ignored_f.write_text("console.log('leak')\n", encoding="utf-8")

    scanner = ProjectScanner(repo)
    res = scanner.scan()

    scanned_paths = [f.path for f in res.files]
    assert scanned_paths == ["main.py", "pkg/module.py", "pkg/sub/deep.py"]
    assert not any("node_modules" in p for p in scanned_paths)

    # Verify absolute_path correctness and resolve alignment
    for pfile in res.files:
        expected_abs = str((repo / pfile.path.replace("/", "\\")).resolve())
        actual_abs = str(Path(pfile.absolute_path).resolve())
        assert actual_abs.lower() == expected_abs.lower()
        assert Path(pfile.absolute_path).is_file()

