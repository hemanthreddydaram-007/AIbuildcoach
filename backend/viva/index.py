"""Project Architectural Index for Milestone 8 Viva Defence Engine.
Derives project-wide category mappings deterministically from M1 files and M2 ProjectGraph.
"""

from typing import List, Dict, Set, Optional
from pathlib import Path

from backend.domain.models import ProjectFile, ProjectGraph
from backend.viva.models import VivaCategory


class ProjectArchitecturalIndex:
    """Lightweight in-memory architectural index mapping repository files to Viva categories."""

    def __init__(
        self,
        files: List[ProjectFile],
        graph: Optional[ProjectGraph] = None,
    ):
        self.files = files
        self.graph = graph
        self._category_map: Dict[VivaCategory, List[str]] = {cat: [] for cat in VivaCategory}
        self._build_index()

    def _build_index(self) -> None:
        """Categorizes files deterministically using path heuristics and graph connectivity."""
        file_paths = [f.path.replace("\\", "/") for f in self.files if not f.is_ignored and not f.is_binary]

        for p in file_paths:
            low = p.lower()

            # Testing & Verification
            if low.startswith("tests/") or "test_" in low or "_test." in low:
                self._category_map[VivaCategory.TESTING_VERIFICATION].append(p)
                continue

            # Security & Auth
            if any(k in low for k in ["auth", "token", "credential", "secret", "jwt", "permission", "security"]):
                self._category_map[VivaCategory.SECURITY_AUTH].append(p)

            # Storage & Persistence
            if any(k in low for k in ["migration", "database", "model", "schema", "store", "repository", "db."]):
                self._category_map[VivaCategory.STORAGE_PERSISTENCE].append(p)

            # API Contracts & Gateways
            if any(k in low for k in ["api", "route", "gateway", "controller", "endpoint", "contract", "handler"]):
                self._category_map[VivaCategory.API_CONTRACTS].append(p)

            # Failure Modes & Resilience
            if any(k in low for k in ["error", "exception", "timeout", "retry", "fallback", "validator"]):
                self._category_map[VivaCategory.FAILURE_MODES].append(p)

            # Dependencies
            if any(k in low for k in ["pyproject.toml", "package.json", "requirements", "setup.py", "pipfile"]):
                self._category_map[VivaCategory.DEPENDENCIES].append(p)

            # Project Purpose & Entrypoints
            if any(k in low for k in ["readme", "main.py", "app.py", "index.", "cli."]):
                self._category_map[VivaCategory.PROJECT_PURPOSE].append(p)

            # Architecture Overview
            if any(k in low for k in ["core/", "domain/", "engine", "service", "workflow"]):
                self._category_map[VivaCategory.ARCHITECTURE_OVERVIEW].append(p)

            # Data Flow
            if any(k in low for k in ["pipeline", "transformer", "stream", "flow", "queue", "detector"]):
                self._category_map[VivaCategory.DATA_FLOW].append(p)

        # Ensure fallback coverage from general files if a category is sparse
        all_non_tests = [p for p in file_paths if not (p.startswith("tests/") or "test_" in p)]
        if not self._category_map[VivaCategory.PROJECT_PURPOSE] and all_non_tests:
            self._category_map[VivaCategory.PROJECT_PURPOSE].extend(all_non_tests[:2])
        if not self._category_map[VivaCategory.ARCHITECTURE_OVERVIEW] and all_non_tests:
            self._category_map[VivaCategory.ARCHITECTURE_OVERVIEW].extend(all_non_tests[:3])
        if not self._category_map[VivaCategory.DATA_FLOW] and all_non_tests:
            self._category_map[VivaCategory.DATA_FLOW].extend(all_non_tests[:2])

    def get_category_files(self, category: VivaCategory, limit: int = 5) -> List[str]:
        """Returns prioritized target files for a specific category."""
        files = self._category_map.get(category, [])
        return files[:limit]

    def get_available_categories(self) -> List[VivaCategory]:
        """Returns categories that have concrete repository evidence."""
        return [cat for cat in VivaCategory if len(self._category_map.get(cat, [])) > 0]
