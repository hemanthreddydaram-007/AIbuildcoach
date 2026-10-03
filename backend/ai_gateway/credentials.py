"""BYOK Credential Store for AI Gateway."""

import json
import os
from pathlib import Path
from typing import Optional

from backend.ai_gateway.exceptions import CredentialMissingError, SecurityConfigurationError

SUSPICIOUS_CONFIG_KEYS = {"api_key", "gemini_key", "secret_key", "apikey", "gemini_api_key", "secret"}


class CredentialStore:
    """Manages BYOK API credentials securely without storing secrets on disk."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path

    def get_gemini_api_key(self, explicit_key: Optional[str] = None) -> str:
        """Resolves the Gemini API key following strict security rules.
        
        Priority:
        1. Explicit in-memory parameter
        2. GEMINI_API_KEY / BUILDCOACH_GEMINI_API_KEY environment variables
        
        Raises:
            SecurityConfigurationError: If an API key is detected in config.json
            CredentialMissingError: If no valid non-empty API key can be found
        """
        # 1. Enforce zero keys in config.json
        if self.config_path and self.config_path.is_file():
            try:
                content = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(content, dict):
                    lowered_keys = {k.lower() for k in content.keys()}
                    if any(sk in lowered_keys for sk in SUSPICIOUS_CONFIG_KEYS):
                        raise SecurityConfigurationError(
                            f"Security violation: Sensitive API keys must NEVER be stored in '{self.config_path}'. "
                            "Remove the credential and use the GEMINI_API_KEY environment variable."
                        )
            except (json.JSONDecodeError, OSError):
                pass  # Malformed or unreadable config is handled by config loader

        # 2. Check explicit in-memory parameter
        if explicit_key and explicit_key.strip():
            return explicit_key.strip()

        # 3. Check environment variables
        env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("BUILDCOACH_GEMINI_API_KEY")
        if env_key and env_key.strip():
            return env_key.strip()

        raise CredentialMissingError(
            "Gemini API key is missing. Set the GEMINI_API_KEY environment variable to use the AI Gateway."
        )

    @staticmethod
    def mask_key(key: Optional[str]) -> str:
        """Returns a sanitized masked representation of an API key for safe diagnostics."""
        if not key:
            return "<none>"
        clean = key.strip()
        if len(clean) <= 8:
            return "***"
        return f"{clean[:4]}...{clean[-4:]}"
