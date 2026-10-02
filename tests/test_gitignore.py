"""Tests for .gitignore and exclusion pattern matching."""

from pathlib import Path
from backend.project_model.gitignore import IgnoreFilter, DEFAULT_EXCLUSIONS


def test_default_exclusions(tmp_path: Path):
    filter_obj = IgnoreFilter(tmp_path)
    
    # Test built-in exclusions
    assert filter_obj.is_ignored(tmp_path / ".git", is_dir=True)
    assert filter_obj.is_ignored(tmp_path / "node_modules", is_dir=True)
    assert filter_obj.is_ignored(tmp_path / "venv", is_dir=True)
    assert filter_obj.is_ignored(tmp_path / "__pycache__", is_dir=True)
    assert filter_obj.is_ignored(tmp_path / ".buildcoach", is_dir=True)
    
    # Normal files should not be ignored
    assert not filter_obj.is_ignored(tmp_path / "main.py", is_dir=False)
    assert not filter_obj.is_ignored(tmp_path / "src" / "index.js", is_dir=False)


def test_custom_gitignore_file(tmp_path: Path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.log\nsecret_data/\n!important.log\n", encoding="utf-8")
    
    filter_obj = IgnoreFilter(tmp_path)
    
    # Matches *.log
    assert filter_obj.is_ignored(tmp_path / "app.log", is_dir=False)
    assert filter_obj.is_ignored(tmp_path / "nested" / "error.log", is_dir=False)
    
    # Negation !important.log
    assert not filter_obj.is_ignored(tmp_path / "important.log", is_dir=False)
    
    # Directory pattern secret_data/
    assert filter_obj.is_ignored(tmp_path / "secret_data", is_dir=True)
    assert filter_obj.is_ignored(tmp_path / "secret_data" / "file.txt", is_dir=False)
