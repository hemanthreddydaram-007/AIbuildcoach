"""Transparent Terminal Integration package (Milestone 12.10)."""

from backend.terminal.protocol import (
    ShellType,
    IntegrationStatus,
    TerminalCommandPayload,
    TerminalSessionRecord,
    TerminalIntegrationStatus,
)
from backend.terminal.session import TerminalSessionManager
from backend.terminal.integration import TerminalIntegrationManager

__all__ = [
    "ShellType",
    "IntegrationStatus",
    "TerminalCommandPayload",
    "TerminalSessionRecord",
    "TerminalIntegrationStatus",
    "TerminalSessionManager",
    "TerminalIntegrationManager",
]
