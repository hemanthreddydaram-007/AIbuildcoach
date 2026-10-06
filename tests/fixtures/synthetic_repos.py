"""Programmatic synthetic repository generators for multi-repo testing and benchmarking."""

import os
import subprocess
import time
import statistics
from pathlib import Path
from typing import Optional, Callable, TypeVar, Tuple, List

T = TypeVar("T")


def _init_git(repo: Path) -> None:
    """Helper to initialize git with safe default identity."""
    subprocess.run(["git", "init"], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def _git_commit(repo: Path, message: str = "initial commit") -> None:
    """Helper to add all files and commit."""
    subprocess.run(["git", "add", "."], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def create_python_fastapi_repo(root: Path, dirty: bool = True) -> Path:
    """Creates a synthetic Python FastAPI backend repository."""
    root.mkdir(parents=True, exist_ok=True)
    _init_git(root)

    # 1. pyproject.toml
    (root / "pyproject.toml").write_text(
        '[project]\nname = "fastapi-service"\nversion = "1.0.0"\ndependencies = ["fastapi", "pydantic"]\n',
        encoding="utf-8",
    )

    # 2. app directory structure
    app_dir = root / "app"
    routes_dir = app_dir / "routes"
    services_dir = app_dir / "services"
    routes_dir.mkdir(parents=True, exist_ok=True)
    services_dir.mkdir(parents=True, exist_ok=True)

    (app_dir / "config.py").write_text(
        '# Configuration settings\nJWT_SECRET = "super-secret-key-12345"\nDATABASE_URL = "sqlite:///./app.db"\n',
        encoding="utf-8",
    )

    (services_dir / "token.py").write_text(
        '"""Token utilities."""\nfrom app.config import JWT_SECRET\n\ndef create_jwt(payload: dict) -> str:\n    return f"token_{payload.get(\'sub\')}"\n',
        encoding="utf-8",
    )

    (routes_dir / "auth.py").write_text(
        '"""Authentication endpoints."""\nfrom app.services.token import create_jwt\n\ndef login_endpoint(user: str) -> dict:\n    return {"token": create_jwt({"sub": user})}\n',
        encoding="utf-8",
    )

    (app_dir / "main.py").write_text(
        '"""Main FastAPI entrypoint."""\nfrom app.routes.auth import login_endpoint\nfrom app.config import DATABASE_URL\n\ndef init_app():\n    print("Starting app...")\n',
        encoding="utf-8",
    )

    _git_commit(root, "Initial Python FastAPI project")

    if dirty:
        # Modify routes/auth.py
        (routes_dir / "auth.py").write_text(
            '"""Authentication endpoints - modified."""\nfrom app.services.token import create_jwt\n\ndef login_endpoint(user: str) -> dict:\n    # Added validation check\n    if not user:\n        raise ValueError("User required")\n    return {"token": create_jwt({"sub": user})}\n',
            encoding="utf-8",
        )
        # Add untracked crypto service
        (services_dir / "crypto.py").write_text(
            '"""Cryptographic hashing helper."""\nimport hashlib\n\ndef hash_password(pw: str) -> str:\n    return hashlib.sha256(pw.encode()).hexdigest()\n',
            encoding="utf-8",
        )

    return root


def create_typescript_node_repo(root: Path, dirty: bool = True) -> Path:
    """Creates a synthetic TypeScript/Node API repository."""
    root.mkdir(parents=True, exist_ok=True)
    _init_git(root)

    # 1. Configs
    (root / "package.json").write_text(
        '{\n  "name": "ts-node-api",\n  "version": "1.0.0",\n  "scripts": { "build": "tsc" }\n}\n',
        encoding="utf-8",
    )
    (root / "tsconfig.json").write_text(
        '{\n  "compilerOptions": { "target": "es2022", "module": "commonjs" }\n}\n',
        encoding="utf-8",
    )
    (root / ".env").write_text(
        'STRIPE_API_KEY="sk_test_9988776655443322"\nPORT=3000\n',
        encoding="utf-8",
    )

    # 2. Source tree
    src_dir = root / "src"
    routes_dir = src_dir / "routes"
    models_dir = src_dir / "models"
    utils_dir = src_dir / "utils"
    routes_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)
    utils_dir.mkdir(parents=True, exist_ok=True)

    (utils_dir / "logger.ts").write_text(
        'export function logInfo(msg: string): void {\n  console.log(`[INFO]: ${msg}`);\n}\n',
        encoding="utf-8",
    )

    (models_dir / "user.ts").write_text(
        'import { logInfo } from "../utils/logger";\n\nexport interface User {\n  id: string;\n  name: string;\n}\n\nexport function createUser(name: string): User {\n  logInfo(`Creating user ${name}`);\n  return { id: "1", name };\n}\n',
        encoding="utf-8",
    )

    # Multiline import syntax testing regex statement accumulator
    (routes_dir / "user.ts").write_text(
        'import {\n  User,\n  createUser\n} from "../models/user";\n\nexport function handleGetUser(): User {\n  return createUser("alice");\n}\n',
        encoding="utf-8",
    )

    (src_dir / "index.ts").write_text(
        'import { handleGetUser } from "./routes/user";\n\nconsole.log(handleGetUser());\n',
        encoding="utf-8",
    )

    _git_commit(root, "Initial TypeScript project")

    if dirty:
        # Staged edit in models/user.ts with secret
        (models_dir / "user.ts").write_text(
            'import { logInfo } from "../utils/logger";\n\n// Secret key\nconst api_key = "sk-test-9988776655443322112233";\n\nexport interface User {\n  id: string;\n  name: string;\n  email?: string;\n}\n\nexport function createUser(name: string): User {\n  logInfo(`Creating user ${name}`);\n  return { id: "1", name, email: `${name}@example.com` };\n}\n',
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "src/models/user.ts"], cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        # Unstaged edit in routes/user.ts
        (routes_dir / "user.ts").write_text(
            'import {\n  User,\n  createUser\n} from "../models/user";\n\nexport function handleGetUser(): User {\n  // Unstaged change\n  return createUser("bob");\n}\n',
            encoding="utf-8",
        )

    return root


def create_react_frontend_repo(root: Path, dirty: bool = True) -> Path:
    """Creates a synthetic React JSX/TSX frontend repository."""
    root.mkdir(parents=True, exist_ok=True)
    _init_git(root)

    # 1. Config & public
    (root / "package.json").write_text(
        '{\n  "name": "react-frontend",\n  "version": "0.1.0",\n  "dependencies": { "react": "^18.2.0" }\n}\n',
        encoding="utf-8",
    )
    public_dir = root / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / "logo.svg").write_text('<svg><circle r="10"/></svg>', encoding="utf-8")

    # 2. Source tree
    src_dir = root / "src"
    components_dir = src_dir / "components"
    hooks_dir = src_dir / "hooks"
    components_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    (hooks_dir / "useAuth.ts").write_text(
        '// User Auth Hook with credential comment\n// Bearer secret-auth-token-998877\nexport function useAuth() {\n  return { isAuthenticated: true, user: "admin" };\n}\n',
        encoding="utf-8",
    )

    (components_dir / "Header.tsx").write_text(
        'export function Header() {\n  return <header><h1>App Header</h1></header>;\n}\n',
        encoding="utf-8",
    )

    (components_dir / "Dashboard.tsx").write_text(
        'import { useAuth } from "../hooks/useAuth";\n\nexport function Dashboard() {\n  const auth = useAuth();\n  return <div>Welcome, {auth.user}</div>;\n}\n',
        encoding="utf-8",
    )

    (src_dir / "App.tsx").write_text(
        'import { Header } from "./components/Header";\nimport { Dashboard } from "./components/Dashboard";\n\nexport function App() {\n  return (\n    <div>\n      <Header />\n      <Dashboard />\n    </div>\n  );\n}\n',
        encoding="utf-8",
    )

    _git_commit(root, "Initial React Frontend project")

    if dirty:
        # Modify Dashboard.tsx
        (components_dir / "Dashboard.tsx").write_text(
            'import { useAuth } from "../hooks/useAuth";\n\nexport function Dashboard() {\n  const auth = useAuth();\n  return (\n    <div>\n      <h2>Dashboard View</h2>\n      <p>Logged in as: {auth.user}</p>\n    </div>\n  );\n}\n',
            encoding="utf-8",
        )
        # Untracked Settings.tsx
        (components_dir / "Settings.tsx").write_text(
            'export function Settings() {\n  return <div>Settings View</div>;\n}\n',
            encoding="utf-8",
        )

    return root


def create_java_gradle_repo(root: Path, dirty: bool = True) -> Path:
    """Creates a synthetic Java Gradle enterprise repository."""
    root.mkdir(parents=True, exist_ok=True)
    _init_git(root)

    # 1. Gradle config
    (root / "build.gradle").write_text(
        'plugins { id "java" }\ngroup = "com.app"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )

    # 2. Source packages
    pkg_dir = root / "src" / "main" / "java" / "com" / "app"
    ctrl_dir = pkg_dir / "controller"
    srv_dir = pkg_dir / "service"
    res_dir = root / "src" / "main" / "resources"
    ctrl_dir.mkdir(parents=True, exist_ok=True)
    srv_dir.mkdir(parents=True, exist_ok=True)
    res_dir.mkdir(parents=True, exist_ok=True)

    (res_dir / "application.properties").write_text(
        'server.port=8080\nspring.datasource.password=db-secret-password-xyz\n',
        encoding="utf-8",
    )

    (pkg_dir / "Application.java").write_text(
        'package com.app;\n\npublic class Application {\n    public static void main(String[] args) {\n        System.out.println("Starting Java App");\n    }\n}\n',
        encoding="utf-8",
    )

    (srv_dir / "AuthService.java").write_text(
        'package com.app.service;\n\npublic class AuthService {\n    public boolean authenticate(String token) {\n        return token != null;\n    }\n}\n',
        encoding="utf-8",
    )

    (ctrl_dir / "AuthController.java").write_text(
        'package com.app.controller;\n\nimport com.app.service.AuthService;\n\npublic class AuthController {\n    private final AuthService authService = new AuthService();\n\n    public boolean login(String token) {\n        return authService.authenticate(token);\n    }\n}\n',
        encoding="utf-8",
    )

    _git_commit(root, "Initial Java Gradle project")

    if dirty:
        # Modify AuthController.java
        (ctrl_dir / "AuthController.java").write_text(
            'package com.app.controller;\n\nimport com.app.service.AuthService;\n\npublic class AuthController {\n    private final AuthService authService = new AuthService();\n\n    public boolean login(String token) {\n        // Added null check\n        if (token == null || token.isEmpty()) return false;\n        return authService.authenticate(token);\n    }\n}\n',
            encoding="utf-8",
        )

    return root


def create_edge_case_repo(root: Path) -> Path:
    """Creates a synthetic repository featuring edge cases (binaries, non-utf8, >1MB file, .env)."""
    root.mkdir(parents=True, exist_ok=True)
    _init_git(root)

    # 1. Base files
    (root / "pyproject.toml").write_text('[project]\nname = "edge-cases"\nversion = "0.0.1"\n', encoding="utf-8")
    (root / "main.py").write_text('def run():\n    print("Running edge case")\n', encoding="utf-8")

    # 2. Binary asset with null bytes
    assets_dir = root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "data.bin").write_bytes(b"\x00\x01\x02\x03\xFF\xFE\x00\x00" * 64)

    # 3. Non-UTF8 file (Latin-1 encoded)
    (assets_dir / "latin1.txt").write_bytes("caf\xe9 and na\xefve".encode("latin-1"))

    # 4. Large file (> 1 MB)
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    large_content = ("LOG ENTRY " * 16 + "\n") * 8000  # ~1.2 MB
    (logs_dir / "large.log").write_text(large_content, encoding="utf-8")

    # 5. Secret configuration
    (root / ".env").write_text(
        'AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\nDEBUG=true\n',
        encoding="utf-8",
    )

    _git_commit(root, "Initial Edge Case Project")

    # Make working tree dirty with secret pattern
    (root / "main.py").write_text(
        'def run():\n    # Modified main with comments\n    api_key = "sk-123456789012345678901234"\n    print("Running edge case modified")\n',
        encoding="utf-8",
    )

    return root


def create_large_benchmark_repo(root: Path, file_count: int = 600) -> Path:
    """Creates a deterministic multi-file repository (500+ files) for performance benchmarking."""
    root.mkdir(parents=True, exist_ok=True)
    _init_git(root)

    (root / "pyproject.toml").write_text(
        f'[project]\nname = "benchmark-repo"\nversion = "1.0.0"\n# Generated {file_count} files\n',
        encoding="utf-8",
    )

    for f in range(file_count):
        code = (
            f'def compute_{f}(x: int) -> int:\n'
            f'    return x * {f + 1}\n'
        )
        (root / f"mod_{f}.py").write_text(code, encoding="utf-8")

    _git_commit(root, f"Benchmark repo with {file_count} files")

    # Modify one file and add one untracked file to create a dirty working tree
    (root / "mod_0.py").write_text(
        'def compute_0(x: int) -> int:\n    return x * 999\n',
        encoding="utf-8",
    )
    (root / "new_util.py").write_text(
        'def helper():\n    return 42\n',
        encoding="utf-8",
    )

    return root


def execute_deterministic_benchmark(
    operation: Callable[[], T],
    warmup_count: int = 3,
    measured_count: int = 5,
) -> Tuple[T, List[float], float]:
    """Shared deterministic benchmark executor used across performance tests.

    Standardizes warm-up execution to stabilize OS filesystem and database caches,
    followed by measured runs and median duration calculation.

    Args:
        operation: Callable returning the benchmark result.
        warmup_count: Number of unmeasured warm-up runs to stabilize caches.
        measured_count: Number of measured runs.

    Returns:
        Tuple of (final_result, durations_ms, median_ms)
    """
    for _ in range(warmup_count):
        operation()

    durations_ms: List[float] = []
    result: Optional[T] = None
    for _ in range(measured_count):
        t0 = time.perf_counter()
        result = operation()
        t1 = time.perf_counter()
        durations_ms.append((t1 - t0) * 1000.0)

    median_ms = statistics.median(durations_ms)
    assert result is not None, "Benchmark operation produced no result"
    return result, durations_ms, median_ms
