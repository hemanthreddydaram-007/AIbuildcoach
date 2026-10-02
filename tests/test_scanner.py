"""Comprehensive tests for ProjectScanner."""

import os
from pathlib import Path
from backend.project_model.scanner import (
    ProjectScanner,
    detect_project_root,
    is_binary_file,
    compute_sha256,
    scan_file,
    MAX_FILE_SIZE_FOR_FULL_SCAN,
)
from backend.domain.models import ScanResult


def test_detect_project_root(tmp_path: Path):
    # Case 1: Marker in directory
    proj_dir = tmp_path / "project_with_marker"
    proj_dir.mkdir()
    (proj_dir / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    nested_dir = proj_dir / "a" / "b" / "c"
    nested_dir.mkdir(parents=True)
    
    found_root = detect_project_root(nested_dir)
    assert found_root == proj_dir.resolve()
    
    # Case 2: No marker anywhere
    empty_tmp = tmp_path / "isolated_no_marker"
    empty_tmp.mkdir()
    assert detect_project_root(empty_tmp) == empty_tmp.resolve()


def test_empty_project_scan(tmp_path: Path):
    db_file = tmp_path / ".buildcoach" / "state.db"
    scanner = ProjectScanner(tmp_path, db_path=db_file)
    result = scanner.scan()
    
    assert isinstance(result, ScanResult)
    assert len(result.files) == 0
    assert result.project.name == tmp_path.name
    assert result.git_state.is_git_repo is False


def test_normal_and_nested_project_scan(tmp_path: Path):
    # Setup nested project structure
    src = tmp_path / "src" / "components"
    src.mkdir(parents=True)
    (src / "Button.jsx").write_text("export const Button = () => <button/>;", encoding="utf-8")
    (tmp_path / "README.md").write_text("# My App", encoding="utf-8")
    
    db_file = tmp_path / ".buildcoach" / "state.db"
    scanner = ProjectScanner(tmp_path, db_path=db_file)
    result = scanner.scan()
    
    assert len(result.files) == 2
    paths = [f.path for f in result.files]
    assert paths == ["README.md", "src/components/Button.jsx"]
    
    button_f = next(f for f in result.files if f.path == "src/components/Button.jsx")
    assert button_f.file_type == ".jsx"
    assert button_f.is_binary is False
    assert button_f.is_large is False
    assert len(button_f.sha256_hash) == 64


def test_ignored_directory_and_gitignore(tmp_path: Path):
    # Create ignored directories
    (tmp_path / "node_modules" / "lodash").mkdir(parents=True)
    (tmp_path / "node_modules" / "lodash" / "index.js").write_text("module.exports = {};")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("binary")
    
    # Custom gitignore
    (tmp_path / ".gitignore").write_text("secret_folder/\n*.tmp\n", encoding="utf-8")
    (tmp_path / "secret_folder").mkdir()
    (tmp_path / "secret_folder" / "pass.txt").write_text("secret")
    (tmp_path / "test.tmp").write_text("temp data")
    
    # Valid file
    (tmp_path / "app.py").write_text("print('hello')", encoding="utf-8")
    
    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    
    file_paths = [f.path for f in result.files]
    assert ".gitignore" in file_paths
    assert "app.py" in file_paths
    assert not any("node_modules" in p for p in file_paths)
    assert not any(".venv" in p for p in file_paths)
    assert not any("secret_folder" in p for p in file_paths)
    assert not any(p.endswith(".tmp") for p in file_paths)


def test_large_file_detection(tmp_path: Path):
    large_file = tmp_path / "big_data.csv"
    # Create file slightly larger than 1 MB threshold
    content = b"a" * (MAX_FILE_SIZE_FOR_FULL_SCAN + 1024)
    large_file.write_bytes(content)
    
    pfile = scan_file(large_file, tmp_path)
    assert pfile is not None
    assert pfile.is_large is True
    assert pfile.file_size == len(content)
    assert len(pfile.sha256_hash) == 64


def test_binary_file_detection(tmp_path: Path):
    bin_file = tmp_path / "image.png"
    bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    
    pfile = scan_file(bin_file, tmp_path)
    assert pfile is not None
    assert pfile.is_binary is True


def test_repeated_scan_idempotence_and_sync(tmp_path: Path):
    db_file = tmp_path / ".buildcoach" / "state.db"
    scanner = ProjectScanner(tmp_path, db_path=db_file)
    
    file1 = tmp_path / "file1.txt"
    file1.write_text("v1 content", encoding="utf-8")
    
    # Run 1
    res1 = scanner.scan()
    assert len(res1.files) == 1
    assert res1.files[0].path == "file1.txt"
    hash_v1 = res1.files[0].sha256_hash
    
    # Run 2 (Unchanged)
    res2 = scanner.scan()
    assert len(res2.files) == 1
    assert res2.files[0].sha256_hash == hash_v1
    
    # Run 3 (Modify file1, add file2)
    file1.write_text("v2 modified content", encoding="utf-8")
    file2 = tmp_path / "file2.txt"
    file2.write_text("file2 content", encoding="utf-8")
    
    res3 = scanner.scan()
    assert len(res3.files) == 2
    f1_updated = next(f for f in res3.files if f.path == "file1.txt")
    assert f1_updated.sha256_hash != hash_v1
    
    # Run 4 (Delete file1)
    file1.unlink()
    res4 = scanner.scan()
    assert len(res4.files) == 1
    assert res4.files[0].path == "file2.txt"


def test_path_boundary_enforcement(tmp_path: Path):
    outside_file = tmp_path.parent / "outside.txt"
    # scan_file should safely reject any file not strictly inside root
    result = scan_file(outside_file, tmp_path)
    assert result is None


def test_permission_error_handling(tmp_path: Path, monkeypatch):
    # Simulate an unreadable / permission denied file
    test_file = tmp_path / "protected.py"
    test_file.write_text("print('secret')", encoding="utf-8")
    
    original_stat = Path.stat
    def mock_stat(self, *args, **kwargs):
        if self.name == "protected.py":
            raise PermissionError("Access is denied")
        return original_stat(self, *args, **kwargs)
        
    monkeypatch.setattr(Path, "stat", mock_stat)
    
    # scan_file should catch PermissionError safely and return None
    res = scan_file(test_file, tmp_path)
    assert res is None


def test_non_utf8_file_handling(tmp_path: Path):
    # File containing raw arbitrary non-UTF-8 bytes
    weird_file = tmp_path / "corrupt.dat"
    weird_file.write_bytes(b"\xff\xfe\xfa\xfb\x80\x81\x82")
    
    pfile = scan_file(weird_file, tmp_path)
    assert pfile is not None
    assert pfile.file_size == 7
    assert pfile.is_binary is False  # No null bytes
    assert len(pfile.sha256_hash) == 64

