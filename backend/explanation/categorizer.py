"""Deterministic file and changeset categorizer for M6."""

import os
from typing import List
from backend.explanation.models import ChangeCategory, DeterministicFileChange


class DeterministicCategorizer:
    """Classifies files and changesets into functional architectural categories deterministically."""

    @staticmethod
    def classify_file(file_path: str) -> ChangeCategory:
        """Classifies a relative file path using deterministic path and extension heuristics."""
        norm_path = file_path.lower().replace("\\", "/")
        base_name = os.path.basename(norm_path)

        # 1. Documentation
        if any(norm_path.startswith(prefix) for prefix in ("docs/", "doc/")) or base_name.startswith("readme") or norm_path.endswith((".md", ".rst", ".txt")):
            return ChangeCategory.DOCUMENTATION

        # 2. Testing
        if "test" in norm_path or "spec" in norm_path:
            return ChangeCategory.TESTING

        # 3. Security & Authentication
        if any(term in norm_path for term in ("auth", "security", "jwt", "token", "login", "password", "oauth", "credential", "permission")):
            return ChangeCategory.SECURITY_AUTH

        # 4. API & External Contracts
        if any(term in norm_path for term in ("api/", "routes/", "endpoints/", "controllers/", "controller/", "proto/", "openapi", "swagger")):
            return ChangeCategory.API_CONTRACT

        # 5. Data & Storage
        if any(term in norm_path for term in ("model/", "models/", "db/", "database/", "migrations/", "migration/", "schema/", "repository/", "entity/", "entities/")) or norm_path.endswith((".sql",)):
            return ChangeCategory.DATA_STORAGE

        # 6. Configuration & Infrastructure
        if any(term in norm_path for term in ("config", "docker", ".github/", "ci/", "k8s/", "terraform/")) or base_name in (
            "pyproject.toml",
            "package.json",
            "package-lock.json",
            "tsconfig.json",
            "makefile",
            "dockerfile",
            "requirements.txt",
            ".gitignore",
        ):
            return ChangeCategory.CONFIG_INFRA

        # 7. Business Logic (default for primary source files)
        if norm_path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rs", ".c", ".cpp", ".cs", ".rb", ".php")):
            return ChangeCategory.BUSINESS_LOGIC

        return ChangeCategory.UNKNOWN

    @staticmethod
    def determine_primary_category(files: List[DeterministicFileChange]) -> ChangeCategory:
        """Determines the primary category for a changeset based on file counts and criticality."""
        if not files:
            return ChangeCategory.UNKNOWN

        # Priority 1: If any critical file is SECURITY_AUTH, escalate to SECURITY_AUTH
        critical_categories = [f.category for f in files if f.is_critical]
        if ChangeCategory.SECURITY_AUTH in critical_categories:
            return ChangeCategory.SECURITY_AUTH

        # Priority 2: Most frequent non-UNKNOWN category among files
        counts = {}
        for f in files:
            if f.category != ChangeCategory.UNKNOWN:
                counts[f.category] = counts.get(f.category, 0) + 1

        if counts:
            sorted_cats = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            return sorted_cats[0][0]

        return ChangeCategory.BUSINESS_LOGIC
