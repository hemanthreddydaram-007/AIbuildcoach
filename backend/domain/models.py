"""Domain models for AI Build Coach Foundation."""

from datetime import datetime, timezone
from typing import Optional, Dict
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SchemaVersion(BaseModel):
    version: int
    applied_at: str = Field(default_factory=utc_now_iso)
    description: str


class GitState(BaseModel):
    is_git_repo: bool = False
    current_branch: Optional[str] = None
    head_commit: Optional[str] = None
    is_dirty: bool = False
    untracked_count: int = 0
    modified_count: int = 0
    staged_count: int = 0


class Project(BaseModel):
    id: str
    name: str
    root_path: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class ProjectFile(BaseModel):
    path: str  # Normalized relative path with forward slashes
    absolute_path: str
    file_size: int
    last_modified: float
    sha256_hash: str
    file_type: str
    is_binary: bool = False
    is_large: bool = False
    is_ignored: bool = False


class ProjectSummary(BaseModel):
    project_id: str
    total_files: int
    total_size_bytes: int
    file_types: Dict[str, int] = Field(default_factory=dict)
    git_state: GitState
    last_scanned: str = Field(default_factory=utc_now_iso)


class ScanResult(BaseModel):
    project: Project
    files: list[ProjectFile]
    git_state: GitState
    scanned_at: str = Field(default_factory=utc_now_iso)
    duration_ms: float = 0.0
