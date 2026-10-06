"""Protocol models and contracts for Transparent Terminal Integration (Milestone 12.10)."""

from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from backend.domain.models import utc_now_iso


class ShellType(str, Enum):
    POWERSHELL = "powershell"
    BASH = "bash"
    ZSH = "zsh"
    CMD = "cmd"


class IntegrationStatus(str, Enum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class TerminalCommandPayload(BaseModel):
    """Payload representing a captured terminal command execution."""
    command: str
    exit_code: int = 0
    duration_ms: float = 0.0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    working_directory: Optional[str] = None
    terminal_session_id: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class TerminalSessionRecord(BaseModel):
    """Identity tracking for an active or recorded shell session."""
    session_id: str
    project_id: str
    shell_type: str = "powershell"
    started_at: str = Field(default_factory=utc_now_iso)
    last_activity_at: str = Field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TerminalIntegrationStatus(BaseModel):
    """Status summary for a project's terminal integration."""
    project_id: str
    status: str = IntegrationStatus.DISABLED.value  # "ENABLED" or "DISABLED"
    shell_type: str = ShellType.POWERSHELL.value
    hook_script_path: Optional[str] = None
    active_session_id: Optional[str] = None
    total_observations: int = 0
    enabled_at: Optional[str] = None
    disabled_at: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
