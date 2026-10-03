"""Tests for Milestone 3 — Development Context and Change Evidence."""

import subprocess
import shutil
from pathlib import Path
import pytest

from backend.domain.models import ChangeType, Project
from backend.project_model.context_detector import ContextDetector
from backend.project_model.scanner import ProjectScanner
from backend.project_model.db import Database


def _setup_git_repo(path: Path) -> Path:
    git_bin = shutil.which("git")
    assert git_bin is not None, "Git binary required for M3 tests"
    def _g(args):
        subprocess.run([git_bin] + args, cwd=str(path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    _g(["init"])
    _g(["config", "user.email", "tester@buildcoach.local"])
    _g(["config", "user.name", "Build Coach Tester"])
    return path


def test_1_clean_working_tree(tmp_path: Path):
    """Test 1: Clean working tree yields 0 file changes and clean status evidence."""
    repo = _setup_git_repo(tmp_path)
    (repo / "README.md").write_text("# Project\nInitial content\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "README.md"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "Initial commit"], cwd=str(repo), check=True)

    detector = ContextDetector(repo, "proj_clean")
    cs = detector.collect()

    assert cs.git_state.is_git_repo is True
    assert cs.git_state.is_dirty is False
    assert len(cs.file_changes) == 0
    assert cs.summary["total_changed_files"] == 0

    status_ev = next(e for e in cs.evidence if e.evidence_type == "GIT_STATUS")
    assert "clean" in status_ev.observation.lower()


def test_2_modified_file_unstaged(tmp_path: Path):
    """Test 2: Unstaged modified file is captured with diff hunks and line ranges."""
    repo = _setup_git_repo(tmp_path)
    auth_file = repo / "auth.py"
    auth_file.write_text("def authenticate():\n    return False\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "auth.py"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "add auth"], cwd=str(repo), check=True)

    # Modify in working tree without committing
    auth_file.write_text("def authenticate():\n    # check tokens\n    return True\n", encoding="utf-8")

    detector = ContextDetector(repo, "proj_mod")
    cs = detector.collect()

    assert cs.git_state.is_dirty is True
    assert len(cs.file_changes) == 1
    fc = cs.file_changes[0]
    assert fc.new_path == "auth.py"
    assert fc.change_type == ChangeType.MODIFIED
    assert fc.is_staged is False
    assert fc.is_untracked is False
    assert len(fc.hunks) >= 1
    assert len(fc.line_ranges) >= 1

    # Verify strictly factual observation
    status_ev = next(e for e in cs.evidence if e.file_path == "auth.py" and e.evidence_type == "GIT_STATUS")
    assert "auth.py is MODIFIED" in status_ev.observation
    diff_ev = next(e for e in cs.evidence if e.file_path == "auth.py" and e.evidence_type == "GIT_DIFF")
    assert "Unstaged hunk" in diff_ev.observation


def test_3_added_file_untracked_and_staged(tmp_path: Path):
    """Test 3: Both untracked and staged added files are captured factually."""
    repo = _setup_git_repo(tmp_path)
    (repo / "init.txt").write_text("init", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "init.txt"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "init"], cwd=str(repo), check=True)

    # Untracked added file
    (repo / "untracked.py").write_text("x = 1\ny = 2\n", encoding="utf-8")

    # Staged added file
    (repo / "staged.py").write_text("def staged(): pass\n", encoding="utf-8")
    subprocess.run([git_bin, "add", "staged.py"], cwd=str(repo), check=True)

    detector = ContextDetector(repo, "proj_added")
    cs = detector.collect()

    assert len(cs.file_changes) == 2
    paths = {f.new_path: f for f in cs.file_changes}

    assert "untracked.py" in paths
    assert paths["untracked.py"].change_type == ChangeType.ADDED
    assert paths["untracked.py"].is_untracked is True
    assert paths["untracked.py"].is_staged is False
    assert paths["untracked.py"].new_line_count == 2
    assert (1, 2) in paths["untracked.py"].line_ranges

    assert "staged.py" in paths
    assert paths["staged.py"].change_type == ChangeType.ADDED
    assert paths["staged.py"].is_staged is True
    assert paths["staged.py"].is_untracked is False


def test_4_deleted_file(tmp_path: Path):
    """Test 4: Deleted file is recognized with DELETED change type."""
    repo = _setup_git_repo(tmp_path)
    to_delete = repo / "deprecated.py"
    to_delete.write_text("def old(): pass\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "deprecated.py"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "add deprecated"], cwd=str(repo), check=True)

    to_delete.unlink()

    detector = ContextDetector(repo, "proj_del")
    cs = detector.collect()

    assert len(cs.file_changes) == 1
    fc = cs.file_changes[0]
    assert fc.new_path == "deprecated.py"
    assert fc.change_type == ChangeType.DELETED
    assert len(fc.hunks) >= 1


def test_5_renamed_file(tmp_path: Path):
    """Test 5: Renamed file records both old_path and new_path."""
    repo = _setup_git_repo(tmp_path)
    old_file = repo / "old_helper.py"
    old_file.write_text("def helper(): return 42\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "old_helper.py"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "add old helper"], cwd=str(repo), check=True)

    subprocess.run([git_bin, "mv", "old_helper.py", "new_helper.py"], cwd=str(repo), check=True)

    detector = ContextDetector(repo, "proj_rename")
    cs = detector.collect()

    assert len(cs.file_changes) == 1
    fc = cs.file_changes[0]
    assert fc.old_path == "old_helper.py"
    assert fc.new_path == "new_helper.py"
    assert fc.change_type == ChangeType.RENAMED
    assert fc.is_staged is True


def test_6_multiple_changed_files(tmp_path: Path):
    """Test 6: Multiple diverse file changes in a single ChangeSet."""
    repo = _setup_git_repo(tmp_path)
    (repo / "f_mod.py").write_text("line1\n", encoding="utf-8")
    (repo / "f_del.py").write_text("delete me\n", encoding="utf-8")
    (repo / "f_ren_old.py").write_text("rename me\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "."], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "base"], cwd=str(repo), check=True)

    (repo / "f_mod.py").write_text("line1\nline2\n", encoding="utf-8")
    (repo / "f_del.py").unlink()
    subprocess.run([git_bin, "mv", "f_ren_old.py", "f_ren_new.py"], cwd=str(repo), check=True)
    (repo / "f_add.py").write_text("new file\n", encoding="utf-8")

    detector = ContextDetector(repo, "proj_multi")
    cs = detector.collect()

    assert cs.summary["total_changed_files"] == 4
    assert cs.summary["added"] == 1
    assert cs.summary["modified"] == 1
    assert cs.summary["deleted"] == 1
    assert cs.summary["renamed"] == 1


def test_7_unstaged_diff(tmp_path: Path):
    """Test 7: Unstaged diff accurately captures hunks and lines."""
    repo = _setup_git_repo(tmp_path)
    target = repo / "calc.py"
    target.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "calc.py"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "add calc"], cwd=str(repo), check=True)

    target.write_text("a = 1\nb = 200\nc = 3\n", encoding="utf-8")

    detector = ContextDetector(repo, "proj_unstaged")
    cs = detector.collect()

    fc = cs.file_changes[0]
    assert len(fc.hunks) == 1
    hunk = fc.hunks[0]
    assert "-b = 2" in hunk.content
    assert "+b = 200" in hunk.content


def test_8_staged_diff(tmp_path: Path):
    """Test 8: Staged diff accurately captures staged hunks."""
    repo = _setup_git_repo(tmp_path)
    target = repo / "service.py"
    target.write_text("class Service:\n    pass\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "service.py"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "add service"], cwd=str(repo), check=True)

    target.write_text("class Service:\n    def start(self): pass\n", encoding="utf-8")
    subprocess.run([git_bin, "add", "service.py"], cwd=str(repo), check=True)

    detector = ContextDetector(repo, "proj_staged")
    cs = detector.collect()

    fc = cs.file_changes[0]
    assert fc.is_staged is True
    assert len(fc.hunks) == 1
    assert "+    def start(self): pass" in fc.hunks[0].content


def test_9_multiple_diff_hunks(tmp_path: Path):
    """Test 9: Multiple non-contiguous diff hunks in a single file."""
    repo = _setup_git_repo(tmp_path)
    target = repo / "large_module.py"
    # Create file with 60 lines
    lines = [f"line_{i} = {i}" for i in range(1, 61)]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "large_module.py"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "add large module"], cwd=str(repo), check=True)

    # Modify line 5 and line 55
    lines[4] = "line_5 = 'MODIFIED_TOP'"
    lines[54] = "line_55 = 'MODIFIED_BOTTOM'"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    detector = ContextDetector(repo, "proj_multi_hunk")
    cs = detector.collect()

    fc = cs.file_changes[0]
    assert len(fc.hunks) == 2
    assert fc.hunks[0].new_start < fc.hunks[1].new_start
    assert len(fc.line_ranges) >= 2


def test_10_binary_file_diff(tmp_path: Path):
    """Test 10: Binary file modification is safely flagged without crashing."""
    repo = _setup_git_repo(tmp_path)
    bin_file = repo / "image.png"
    bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR_V1")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "image.png"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "add binary"], cwd=str(repo), check=True)

    bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR_V2_MODIFIED")

    detector = ContextDetector(repo, "proj_bin")
    cs = detector.collect()

    fc = cs.file_changes[0]
    assert fc.new_path == "image.png"
    assert fc.is_binary is True


def test_11_empty_diff(tmp_path: Path):
    """Test 11: Empty diff on clean repository returns 0 changes and clean status."""
    repo = _setup_git_repo(tmp_path)
    (repo / "f.txt").write_text("hello", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "."], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "commit"], cwd=str(repo), check=True)

    detector = ContextDetector(repo, "proj_empty_diff")
    cs = detector.collect()

    assert len(cs.file_changes) == 0
    assert cs.summary["total_changed_files"] == 0


def test_12_missing_git_repository(tmp_path: Path):
    """Test 12: Missing .git directory safely produces non-git ChangeSet without error."""
    non_git_dir = tmp_path / "not_git"
    non_git_dir.mkdir()
    (non_git_dir / "code.py").write_text("print('hello')", encoding="utf-8")

    detector = ContextDetector(non_git_dir, "proj_no_git")
    cs = detector.collect()

    assert cs.git_state.is_git_repo is False
    assert len(cs.file_changes) == 0
    assert cs.summary["is_git_repo"] is False
    assert any("not a git repository" in e.observation.lower() for e in cs.evidence)


def test_13_git_command_failure(tmp_path: Path, monkeypatch):
    """Test 13: Git execution failure falls back gracefully."""
    repo = _setup_git_repo(tmp_path)
    detector = ContextDetector(repo, "proj_fail")

    # Point git_bin to a nonexistent executable
    detector.git_bin = str(tmp_path / "nonexistent_git.exe")
    cs = detector.collect()

    assert cs.git_state.is_git_repo is False
    assert len(cs.file_changes) == 0


def test_14_deterministic_changeset_ids(tmp_path: Path):
    """Test 14: Repeated collection on identical working tree produces identical ChangeSet ID."""
    repo = _setup_git_repo(tmp_path)
    (repo / "main.py").write_text("x = 1\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "main.py"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "init"], cwd=str(repo), check=True)

    (repo / "main.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "new.py").write_text("y = 10\n", encoding="utf-8")

    d1 = ContextDetector(repo, "proj_det")
    cs1 = d1.collect()

    d2 = ContextDetector(repo, "proj_det")
    cs2 = d2.collect()

    assert cs1.id == cs2.id
    assert [fc.id for fc in cs1.file_changes] == [fc.id for fc in cs2.file_changes]


def test_15_deterministic_evidence_ids(tmp_path: Path):
    """Test 15: Evidence IDs are deterministic and repeatable."""
    repo = _setup_git_repo(tmp_path)
    (repo / "mod.py").write_text("v1\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "."], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "v1"], cwd=str(repo), check=True)

    (repo / "mod.py").write_text("v2\n", encoding="utf-8")

    d = ContextDetector(repo, "proj_ev")
    cs1 = d.collect()
    cs2 = d.collect()

    assert [e.id for e in cs1.evidence] == [e.id for e in cs2.evidence]
    assert [e.observation for e in cs1.evidence] == [e.observation for e in cs2.evidence]


def test_16_repeated_context_collection_and_persistence(tmp_path: Path):
    """Test 16: Persistence to SQLite, retrieval, and re-saving idempotency."""
    repo = _setup_git_repo(tmp_path)
    (repo / "f.py").write_text("a = 1\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "."], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "init"], cwd=str(repo), check=True)

    (repo / "f.py").write_text("a = 2\n", encoding="utf-8")

    db_path = repo / ".buildcoach" / "state.db"
    db = Database(db_path)
    db.upsert_project(Project(id="p1", name="p1", root_path=str(repo)))

    detector = ContextDetector(repo, "p1")
    cs = detector.collect()

    # Save to database
    db.save_change_set(cs)

    # Retrieve from database
    latest = db.get_latest_change_set("p1")
    assert latest is not None
    assert latest.id == cs.id
    assert len(latest.file_changes) == len(cs.file_changes)
    assert len(latest.evidence) == len(cs.evidence)

    # Re-save idempotency
    db.save_change_set(cs)
    latest2 = db.get_latest_change_set("p1")
    assert latest2 is not None
    assert latest2.id == cs.id


def test_17_synchronization_after_file_reverted(tmp_path: Path):
    """Test 17: Reverting an uncommitted change clears the change set."""
    repo = _setup_git_repo(tmp_path)
    calc = repo / "calc.py"
    calc.write_text("initial code\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "calc.py"], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "add calc"], cwd=str(repo), check=True)

    # Modify
    calc.write_text("temporary edit\n", encoding="utf-8")
    d1 = ContextDetector(repo, "p_sync")
    cs1 = d1.collect()
    assert len(cs1.file_changes) == 1

    # Revert back
    calc.write_text("initial code\n", encoding="utf-8")
    d2 = ContextDetector(repo, "p_sync")
    cs2 = d2.collect()
    assert len(cs2.file_changes) == 0
    assert cs2.git_state.is_dirty is False


def test_18_scanner_integrates_m3_changeset(tmp_path: Path):
    """Test 18: ProjectScanner automatically collects and persists ChangeSet."""
    repo = _setup_git_repo(tmp_path)
    (repo / "app.py").write_text("import os\n", encoding="utf-8")
    git_bin = shutil.which("git")
    subprocess.run([git_bin, "add", "."], cwd=str(repo), check=True)
    subprocess.run([git_bin, "commit", "-m", "initial"], cwd=str(repo), check=True)

    # Introduce uncommitted change
    (repo / "app.py").write_text("import os\nimport sys\n", encoding="utf-8")

    scanner = ProjectScanner(repo, db_path=repo / ".buildcoach" / "state.db")
    result = scanner.scan()

    assert result.change_set is not None
    assert len(result.change_set.file_changes) == 1
    assert result.change_set.file_changes[0].new_path == "app.py"

    # Verify persisted in database
    db = Database(repo / ".buildcoach" / "state.db")
    saved_cs = db.get_latest_change_set(result.project.id)
    assert saved_cs is not None
    assert saved_cs.id == result.change_set.id
