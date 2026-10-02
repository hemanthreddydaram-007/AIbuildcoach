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


def test_root_anchored_leading_slash_patterns(tmp_path: Path):
    from backend.project_model.gitignore import GitIgnoreRule

    # 1. Direct rule test for /build and /build/
    rule_build = GitIgnoreRule("/build", tmp_path)
    assert rule_build.matches("build", is_dir=True)
    assert rule_build.matches("build/app.js", is_dir=False)
    assert not rule_build.matches("src/build", is_dir=True)
    assert not rule_build.matches("src/build/app.js", is_dir=False)

    rule_build_dir = GitIgnoreRule("/build/", tmp_path, directory_only=True)
    assert rule_build_dir.matches("build", is_dir=True)
    assert not rule_build_dir.matches("build", is_dir=False)  # Regular file named build not matched
    assert rule_build_dir.matches("build/chunk.js", is_dir=False)
    assert not rule_build_dir.matches("src/build", is_dir=True)

    # 2. Direct rule test for /secrets and /temp/*.log
    rule_secrets = GitIgnoreRule("/secrets", tmp_path)
    assert rule_secrets.matches("secrets", is_dir=True)
    assert rule_secrets.matches("secrets/key.txt", is_dir=False)
    assert not rule_secrets.matches("app/secrets", is_dir=True)
    assert not rule_secrets.matches("app/secrets/key.txt", is_dir=False)

    rule_temp = GitIgnoreRule("/temp/*.log", tmp_path)
    assert rule_temp.matches("temp/server.log", is_dir=False)
    assert not rule_temp.matches("temp/sub/server.log", is_dir=False)
    assert not rule_temp.matches("nested/temp/server.log", is_dir=False)
    assert not rule_temp.matches("temp/data.csv", is_dir=False)

    # 3. Integration with IgnoreFilter loading from .gitignore
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        "/secrets\n"
        "/custom_build_dir/\n"
        "/temp/*.log\n",
        encoding="utf-8",
    )

    filter_obj = IgnoreFilter(tmp_path)

    # /secrets root matches, nested app/secrets does not match
    assert filter_obj.is_ignored(tmp_path / "secrets", is_dir=True)
    assert filter_obj.is_ignored(tmp_path / "secrets" / "key.txt", is_dir=False)
    assert not filter_obj.is_ignored(tmp_path / "app" / "secrets", is_dir=True)
    assert not filter_obj.is_ignored(tmp_path / "app" / "secrets" / "key.txt", is_dir=False)

    # /custom_build_dir/ directory matches, but not regular file
    assert filter_obj.is_ignored(tmp_path / "custom_build_dir", is_dir=True)
    assert filter_obj.is_ignored(tmp_path / "custom_build_dir" / "chunk.js", is_dir=False)
    assert not filter_obj.is_ignored(tmp_path / "custom_build_dir", is_dir=False)
    assert not filter_obj.is_ignored(tmp_path / "nested" / "custom_build_dir", is_dir=True)

    # /temp/*.log matches log files directly in root temp, but not in subdirectories
    assert filter_obj.is_ignored(tmp_path / "temp" / "server.log", is_dir=False)
    assert not filter_obj.is_ignored(tmp_path / "temp" / "sub" / "server.log", is_dir=False)
    assert not filter_obj.is_ignored(tmp_path / "nested" / "temp" / "server.log", is_dir=False)

    # Paths outside the repository root are safely rejected / considered ignored
    outside_dir = tmp_path.parent / "outside_project"
    assert filter_obj.is_ignored(outside_dir, is_dir=True)
    assert filter_obj.is_ignored(outside_dir / "file.txt", is_dir=False)
