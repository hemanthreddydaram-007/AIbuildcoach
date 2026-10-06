"""Deterministic runtime output parsing for errors and test frameworks (Milestone 12.9)."""

import re
from typing import Optional, Dict, Any, List, Tuple
from backend.observation.normalizer import (
    parse_stack_trace_and_error,
    compute_deterministic_id,
    compute_error_signature,
    KNOWN_EXCEPTION_TYPES,
)
from backend.observation.models import NormalizedRuntimeError

# Match HTTP error statuses in output, e.g., HTTP 500, HTTP/1.1 404 Not Found, "GET /api 500"
RE_HTTP_STATUS_ERROR = re.compile(
    r'(?:HTTP/\d\.\d\s+|status\s*(?:code)?\s*[:=]\s*|\"?[A-Z]+\s+[^\s\"]+\s+)(4\d\d|5\d\d)\b'
)

# Common JS/Node error formats: TypeError: ..., ReferenceError: ..., Error: ...
RE_JS_ERROR = re.compile(
    r'^(?:[A-Z][a-zA-Z0-9_]*:)?\s*([A-Z][a-zA-Z0-9_]*(?:Error|Exception)):\s*(.*)$',
    re.MULTILINE
)

# Pytest summary line patterns
# e.g., "=== 1 failed, 10 passed in 2.34s ==="
# e.g., "=== 5 passed in 0.12s ==="
# e.g., "=== 2 errors in 0.45s ==="
RE_PYTEST_SUMMARY = re.compile(
    r'=+\s*(?:(?P<failed>\d+)\s+failed)?(?:,\s*)?(?P<passed>\d+)?\s*(?:passed)?(?:,\s*)?(?:(?P<error>\d+)\s+error(?:s)?)?.*?in\s+[\d\.]+s\s*=+',
    re.IGNORECASE
)

# Generic / npm / node test patterns
# e.g. "# pass 64", "# fail 0" (Node test runner / tap)
RE_NODE_TAP_SUMMARY = re.compile(
    r'#\s*pass\s+(?P<pass>\d+).*?#\s*fail\s+(?P<fail>\d+)',
    re.DOTALL | re.IGNORECASE
)

# Jest / Mocha test summary
# Tests: 1 failed, 4 passed, 5 total
RE_JEST_SUMMARY = re.compile(
    r'Tests:\s*(?:(?P<failed>\d+)\s+failed,\s*)?(?P<passed>\d+)\s+passed,\s*(?P<total>\d+)\s+total',
    re.IGNORECASE
)

# JUnit / pytest failures / individual tests:
# FAILED tests/test_foo.py::test_bar
RE_PYTEST_FAILURES = re.compile(
    r'^FAILED\s+([^\s]+)',
    re.MULTILINE
)


class ParsedTestSummary:
    """Structured test runner outcome parsed deterministically."""
    def __init__(
        self,
        framework: str,
        status: str,  # "PASSED", "FAILED", "UNKNOWN"
        passed_count: int = 0,
        failed_count: int = 0,
        total_count: int = 0,
        failed_tests: Optional[List[str]] = None,
    ):
        self.framework = framework
        self.status = status
        self.passed_count = passed_count
        self.failed_count = failed_count
        self.total_count = total_count
        self.failed_tests = failed_tests or []


def parse_runtime_error(output: str) -> Optional[NormalizedRuntimeError]:
    """Deterministically detects runtime errors from command output.
    
    Supports:
    - Python exceptions and stack traces (ModuleNotFoundError, ImportError, SyntaxError,
      TypeError, NameError, KeyError, ConnectionError, AssertionError, etc.)
    - HTTP 4xx and 5xx status codes
    - JavaScript / Node runtime errors
    """
    if not output or not output.strip():
        return None

    # 1. Try standard Python stack trace and exception parser
    py_err = parse_stack_trace_and_error(output)
    if py_err:
        return py_err

    # 2. Check for JS/Node runtime errors
    js_match = RE_JS_ERROR.search(output)
    if js_match:
        err_kind = js_match.group(1).strip()
        err_msg = js_match.group(2).strip()
        sig = compute_error_signature(err_kind, err_msg)
        return NormalizedRuntimeError(
            error_kind=err_kind,
            message=err_msg,
            error_signature=sig,
            raw_snippet=output[-500:].strip(),
        )

    # 3. Check for explicit HTTP 4xx / 5xx errors
    http_match = RE_HTTP_STATUS_ERROR.search(output)
    if http_match:
        code = http_match.group(1)
        err_kind = f"HTTP_{code}"
        err_msg = f"HTTP {code} Error"
        sig = compute_error_signature(err_kind, err_msg)
        return NormalizedRuntimeError(
            error_kind=err_kind,
            message=err_msg,
            error_signature=sig,
            raw_snippet=output[-500:].strip(),
        )

    return None


def parse_test_results(command_str: str, stdout: str, stderr: str, exit_code: int) -> Optional[ParsedTestSummary]:
    """Detects if a command was a test runner and deterministically extracts results.
    
    Supports:
    - pytest
    - npm test / node:test / tap
    - jest / vitest / mocha
    """
    combined = f"{stdout}\n{stderr}"
    cmd_lower = command_str.lower()
    is_test_command = any(k in cmd_lower for k in ("pytest", "npm test", "node --test", "yarn test", "pnpm test", "jest", "vitest", "python -m unittest"))

    # 1. Pytest detection
    if "pytest" in cmd_lower or "=== test session starts ===" in combined:
        failed_tests = RE_PYTEST_FAILURES.findall(combined)
        # Check summary lines
        for line in reversed(combined.splitlines()):
            line_str = line.strip()
            if line_str.startswith("=") and line_str.endswith("="):
                # Check for failed
                if "failed" in line_str.lower() or "error" in line_str.lower():
                    # Parse failed count
                    m = re.search(r'(\d+)\s+failed', line_str, re.IGNORECASE)
                    fail_cnt = int(m.group(1)) if m else len(failed_tests) or 1
                    m_pass = re.search(r'(\d+)\s+passed', line_str, re.IGNORECASE)
                    pass_cnt = int(m_pass.group(1)) if m_pass else 0
                    return ParsedTestSummary(
                        framework="pytest",
                        status="FAILED",
                        passed_count=pass_cnt,
                        failed_count=fail_cnt,
                        total_count=pass_cnt + fail_cnt,
                        failed_tests=failed_tests,
                    )
                elif "passed" in line_str.lower() and "failed" not in line_str.lower():
                    m_pass = re.search(r'(\d+)\s+passed', line_str, re.IGNORECASE)
                    pass_cnt = int(m_pass.group(1)) if m_pass else 1
                    return ParsedTestSummary(
                        framework="pytest",
                        status="PASSED",
                        passed_count=pass_cnt,
                        failed_count=0,
                        total_count=pass_cnt,
                        failed_tests=[],
                    )

    # 2. Node test runner / TAP format
    tap_match = RE_NODE_TAP_SUMMARY.search(combined)
    if tap_match:
        p_cnt = int(tap_match.group("pass"))
        f_cnt = int(tap_match.group("fail"))
        status = "FAILED" if f_cnt > 0 else "PASSED"
        return ParsedTestSummary(
            framework="node-test",
            status=status,
            passed_count=p_cnt,
            failed_count=f_cnt,
            total_count=p_cnt + f_cnt,
        )

    # 3. Jest format
    jest_match = RE_JEST_SUMMARY.search(combined)
    if jest_match:
        f_cnt = int(jest_match.group("failed") or 0)
        p_cnt = int(jest_match.group("passed") or 0)
        tot = int(jest_match.group("total") or (p_cnt + f_cnt))
        status = "FAILED" if f_cnt > 0 else "PASSED"
        return ParsedTestSummary(
            framework="jest",
            status=status,
            passed_count=p_cnt,
            failed_count=f_cnt,
            total_count=tot,
        )

    # 4. If explicitly identified as test command by command invocation but output unclear
    if is_test_command:
        if exit_code == 0:
            return ParsedTestSummary(
                framework="generic-test",
                status="PASSED",
            )
        else:
            return ParsedTestSummary(
                framework="generic-test",
                status="FAILED",
            )

    return None
