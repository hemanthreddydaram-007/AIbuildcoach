"""Project Model package exports."""

from backend.project_model.scanner import ProjectScanner, detect_project_root, scan_file
from backend.project_model.git_detector import detect_git_state
from backend.project_model.gitignore import IgnoreFilter, DEFAULT_EXCLUSIONS
from backend.project_model.db import Database
from backend.project_model.migrations import apply_migrations, get_current_schema_version

__all__ = [
    "ProjectScanner",
    "detect_project_root",
    "scan_file",
    "detect_git_state",
    "IgnoreFilter",
    "DEFAULT_EXCLUSIONS",
    "Database",
    "apply_migrations",
    "get_current_schema_version",
]
