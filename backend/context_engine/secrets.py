"""Deterministic secret detection and redaction engine for AI Build Coach."""

import re
from typing import Tuple, Dict, Any


class SecretCategory:
    API_KEY = "API_KEY"
    BEARER_TOKEN = "BEARER_TOKEN"
    PASSWORD = "PASSWORD"
    PRIVATE_KEY = "PRIVATE_KEY"
    ENV_CREDENTIAL = "ENV_CREDENTIAL"


# Precompiled regular expressions for deterministic secret detection
# 1. Private keys (multiline)
RE_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    re.MULTILINE,
)

# 2. Bearer tokens in headers or source code
RE_BEARER = re.compile(
    r"(?i)(\bBearer\s+)([A-Za-z0-9_\-\.]{15,})"
)

# 3. Known high-entropy API key patterns
RE_KNOWN_API_KEYS = re.compile(
    r"\b("
    r"sk-[a-zA-Z0-9_\-]{20,}|"
    r"AIza[0-9A-Za-z\-_]{35}|"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}|"
    r"gh[pousr]_[A-Za-z0-9_]{36,255}|"
    r"github_pat_[0-9a-zA-Z_]{82}|"
    r"xox[baprs]-[0-9a-zA-Z]{10,48}"
    r")\b"
)

# 4. Password assignments: password = "...", passwd = '...', pwd: '...', pass = '...'
RE_PASSWORD_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|pass)\b\s*[:=]\s*)([\"'])([^\"'\r\n]+)\2"
)

# 5. Common credential assignments: api_key = "...", token = "...", secret = "..."
RE_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api_key|apikey|secret_key|client_secret|auth_token|access_token|secret)\b\s*[:=]\s*)([\"'])([^\"'\r\n]{6,})\2"
)

# 6. .env style key=value assignments: e.g. DB_PASSWORD=xyz, DB_PASS=xyz, API_KEY=xyz
RE_ENV_CREDENTIAL = re.compile(
    r"(?m)^([ \t]*(?:export\s+)?(?:[A-Z0-9_]*(?:KEY|SECRET|PASSWORD|PASSWD|PASS|TOKEN|CREDENTIAL|PRIVATE)[A-Z0-9_]*)\s*=\s*)([\"']?)([^\s\"'#\r\n]{4,})\2"
)


def detect_and_redact(content: str) -> Tuple[str, bool, Dict[str, Any]]:
    """Detects sensitive values in content and replaces them with deterministic placeholders.
    
    Returns:
        (redacted_content, was_redacted, redaction_summary)
    
    IMPORTANT:
        Raw secret values are NEVER stored, returned, or logged.
        Only counts and detected categories are tracked.
    """
    if not content:
        return content, False, {"total_secrets_detected": 0, "categories": {}}

    categories: Dict[str, int] = {}
    current_content = content

    # 1. Private keys
    matches_pk = RE_PRIVATE_KEY.findall(current_content)
    if matches_pk:
        count = len(matches_pk)
        categories[SecretCategory.PRIVATE_KEY] = categories.get(SecretCategory.PRIVATE_KEY, 0) + count
        current_content = RE_PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", current_content)

    # 2. .env credentials
    def _redact_env(m: re.Match) -> str:
        val = m.group(3)
        if val in ("[REDACTED]", "[REDACTED_PRIVATE_KEY]"):
            return m.group(0)
        categories[SecretCategory.ENV_CREDENTIAL] = categories.get(SecretCategory.ENV_CREDENTIAL, 0) + 1
        return f"{m.group(1)}{m.group(2)}[REDACTED]{m.group(2)}"

    current_content = RE_ENV_CREDENTIAL.sub(_redact_env, current_content)

    # 3. Password assignments
    def _redact_pwd(m: re.Match) -> str:
        val = m.group(3)
        if val in ("[REDACTED]", "[REDACTED_PRIVATE_KEY]"):
            return m.group(0)
        categories[SecretCategory.PASSWORD] = categories.get(SecretCategory.PASSWORD, 0) + 1
        return f"{m.group(1)}{m.group(2)}[REDACTED]{m.group(2)}"

    current_content = RE_PASSWORD_ASSIGNMENT.sub(_redact_pwd, current_content)

    # 4. Credential assignments
    def _redact_cred(m: re.Match) -> str:
        val = m.group(3)
        if val in ("[REDACTED]", "[REDACTED_PRIVATE_KEY]"):
            return m.group(0)
        categories[SecretCategory.API_KEY] = categories.get(SecretCategory.API_KEY, 0) + 1
        return f"{m.group(1)}{m.group(2)}[REDACTED]{m.group(2)}"

    current_content = RE_CREDENTIAL_ASSIGNMENT.sub(_redact_cred, current_content)

    # 5. Bearer tokens
    def _redact_bearer(m: re.Match) -> str:
        val = m.group(2)
        if val == "[REDACTED]":
            return m.group(0)
        categories[SecretCategory.BEARER_TOKEN] = categories.get(SecretCategory.BEARER_TOKEN, 0) + 1
        return f"{m.group(1)}[REDACTED]"

    current_content = RE_BEARER.sub(_redact_bearer, current_content)

    # 6. Known standalone API key formats
    def _redact_api_key(m: re.Match) -> str:
        val = m.group(1)
        if val in ("[REDACTED]", "[REDACTED_PRIVATE_KEY]"):
            return m.group(0)
        categories[SecretCategory.API_KEY] = categories.get(SecretCategory.API_KEY, 0) + 1
        return "[REDACTED]"

    current_content = RE_KNOWN_API_KEYS.sub(_redact_api_key, current_content)

    total_detected = sum(categories.values())
    was_redacted = total_detected > 0

    summary = {
        "total_secrets_detected": total_detected,
        "categories": categories,
    }
    return current_content, was_redacted, summary
