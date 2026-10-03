"""Safe, deterministic project scanner and indexer."""

import os
import time
import hashlib
from pathlib import Path
from typing import Optional, List, Tuple

from backend.domain.models import Project, ProjectFile, GitState, ScanResult
from backend.project_model.gitignore import IgnoreFilter, DEFAULT_EXCLUSIONS
from backend.project_model.git_detector import detect_git_state
from backend.project_model.db import Database
from backend.project_model.graph_builder import ProjectGraphBuilder
from backend.project_model.context_detector import ContextDetector

MAX_FILE_SIZE_FOR_FULL_SCAN = 1024 * 1024  # 1 MB threshold for large file tagging
SAMPLE_CHUNK_SIZE = 8192  # 8 KB for binary detection
HASH_CHUNK_SIZE = 65536  # 64 KB for streaming SHA-256

PROJECT_ROOT_MARKERS = [
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "requirements.txt",
    "CMakeLists.txt",
]


def detect_project_root(start_path: Path) -> Path:
    """Traverses upward from start_path to find the project root marker.
    
    If start_path is a file, begins searching from its parent directory.
    If nonexistent or inaccessible, safely checks parent directories or falls back.
    """
    try:
        resolved = start_path.resolve()
    except (OSError, ValueError):
        return start_path

    if resolved.is_file():
        current = resolved.parent
    elif not resolved.exists():
        current = resolved.parent if resolved.suffix or resolved.parent.exists() else resolved
    else:
        current = resolved

    fallback = current

    while True:
        try:
            for marker in PROJECT_ROOT_MARKERS:
                candidate = current / marker
                if candidate.exists():
                    return current
        except (OSError, PermissionError):
            pass

        parent = current.parent
        if parent == current:
            # Reached filesystem root
            break
        current = parent

    return fallback


def is_binary_file(file_path: Path) -> bool:
    """Detects whether a file is binary by inspecting for null bytes in the first 8 KB."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(SAMPLE_CHUNK_SIZE)
            return b"\x00" in chunk
    except (IOError, OSError, PermissionError):
        return False


def compute_sha256(file_path: Path) -> str:
    """Computes SHA-256 hash using streaming chunks to conserve memory."""
    hasher = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(HASH_CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()
    except (IOError, OSError, PermissionError):
        return ""


def scan_file(file_path: Path, root_path: Path) -> Optional[ProjectFile]:
    """Inspects a single file safely and returns a ProjectFile record."""
    try:
        resolved = file_path.resolve()
        # Verify path boundary to prevent path traversal
        rel_path = str(resolved.relative_to(root_path)).replace("\\", "/")
    except (ValueError, OSError):
        return None

    try:
        stat_res = file_path.stat()
    except (IOError, OSError, PermissionError):
        return None

    file_size = stat_res.st_size
    last_modified = stat_res.st_mtime
    file_type = file_path.suffix.lower() if file_path.suffix else "no_ext"
    is_large = file_size > MAX_FILE_SIZE_FOR_FULL_SCAN
    is_binary = is_binary_file(file_path)
    sha256_hash = compute_sha256(file_path)

    return ProjectFile(
        path=rel_path,
        absolute_path=str(resolved),
        file_size=file_size,
        last_modified=last_modified,
        sha256_hash=sha256_hash,
        file_type=file_type,
        is_binary=is_binary,
        is_large=is_large,
        is_ignored=False,
    )


class ProjectScanner:
    def __init__(
        self,
        project_root: Path,
        db_path: Optional[Path] = None,
        custom_exclusions: Optional[List[str]] = None,
    ):
        self.project_root = project_root.resolve()
        self.ignore_filter = IgnoreFilter(self.project_root, custom_exclusions)
        if db_path is None:
            db_path = self.project_root / ".buildcoach" / "state.db"
        self.db = Database(db_path)

    def scan(self) -> ScanResult:
        start_time = time.perf_counter()
        project_name = self.project_root.name
        project_id = hashlib.sha256(str(self.project_root).encode("utf-8")).hexdigest()[:16]

        project = Project(
            id=project_id,
            name=project_name,
            root_path=str(self.project_root),
        )

        scanned_files: List[ProjectFile] = []
        scan_errors: List[str] = []

        def _on_walk_error(err: OSError):
            scan_errors.append(f"Cannot access directory: {err}")

        # Deterministic walk: sorted directory traversal
        for root, dirs, files in os.walk(
            self.project_root,
            topdown=True,
            followlinks=False,
            onerror=_on_walk_error,
        ):
            root_path_obj = Path(root)

            # Filter out ignored directories in-place to prevent descending into them
            dirs.sort()
            dirs[:] = [
                d
                for d in dirs
                if not self.ignore_filter.is_ignored(root_path_obj / d, is_dir=True)
            ]

            files.sort()
            for filename in files:
                file_path_obj = root_path_obj / filename
                if self.ignore_filter.is_ignored(file_path_obj, is_dir=False):
                    continue

                pfile = scan_file(file_path_obj, self.project_root)
                if pfile is not None:
                    scanned_files.append(pfile)

        # Sort scanned files deterministically by relative path
        scanned_files.sort(key=lambda f: f.path)

        # Detect Git state safely (read-only)
        git_state = detect_git_state(self.project_root)

        # Persist to local SQLite state.db
        self.db.upsert_project(project)
        self.db.sync_files(project.id, scanned_files)
        self.db.record_git_state(project.id, git_state)

        # Build and synchronize Project Graph
        graph_builder = ProjectGraphBuilder(
            project_root=self.project_root,
            project=project,
            files=scanned_files,
            db=self.db,
        )
        project_graph = graph_builder.build()

        # Collect and persist development context (M3: ChangeSet and Evidence)
        context_detector = ContextDetector(self.project_root, project.id)
        change_set = context_detector.collect()
        self.db.save_change_set(change_set)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        self.db.record_scan_run(project.id, len(scanned_files), duration_ms)

        return ScanResult(
            project=project,
            files=scanned_files,
            git_state=git_state,
            graph=project_graph,
            change_set=change_set,
            duration_ms=duration_ms,
            errors=scan_errors,
        )
