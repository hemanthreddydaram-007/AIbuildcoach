"""Comprehensive deterministic tests for Milestone 2 - Project Graph Foundation."""

from pathlib import Path
import pytest

from backend.domain.models import (
    Project,
    ProjectFile,
    ProjectGraph,
    NodeType,
    EdgeType,
    ProvenanceRecord,
)
from backend.project_model.db import Database
from backend.project_model.scanner import ProjectScanner
from backend.project_model.graph_builder import ProjectGraphBuilder
from backend.project_model.relationship_extractor import extract_file_relationships


def test_project_directory_file_nodes_and_contains(tmp_path: Path):
    """Tests 1, 2, 3, 4: Project, Directory, File node creation and CONTAINS edges."""
    src = tmp_path / "src" / "utils"
    src.mkdir(parents=True)
    (src / "math.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test Project\n", encoding="utf-8")

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()

    graph = result.graph
    assert graph is not None

    # 1. Project node
    proj_node_id = f"project:{result.project.id}"
    proj_node = graph.get_node(proj_node_id)
    assert proj_node is not None
    assert proj_node.node_type == NodeType.PROJECT
    assert proj_node.name == tmp_path.name

    # 2. Directory nodes
    dir_src = graph.get_node("dir:src")
    dir_utils = graph.get_node("dir:src/utils")
    assert dir_src is not None
    assert dir_src.node_type == NodeType.DIRECTORY
    assert dir_utils is not None
    assert dir_utils.node_type == NodeType.DIRECTORY

    # 3. File nodes
    file_math = graph.get_node("file:src/utils/math.py")
    file_readme = graph.get_node("file:README.md")
    assert file_math is not None
    assert file_math.node_type == NodeType.FILE
    assert file_readme is not None
    assert file_readme.node_type == NodeType.FILE

    # 4. CONTAINS relationships
    # Project -> src, Project -> README.md
    out_proj = {e.target_node_id for e in graph.get_out_edges(proj_node_id, EdgeType.CONTAINS)}
    assert "dir:src" in out_proj
    assert "file:README.md" in out_proj

    # src -> src/utils
    out_src = {e.target_node_id for e in graph.get_out_edges("dir:src", EdgeType.CONTAINS)}
    assert "dir:src/utils" in out_src

    # src/utils -> math.py
    out_utils = {e.target_node_id for e in graph.get_out_edges("dir:src/utils", EdgeType.CONTAINS)}
    assert "file:src/utils/math.py" in out_utils


def test_python_import_extraction(tmp_path: Path):
    """Test 5: Python import extraction (internal file resolution + external modules)."""
    app = tmp_path / "app"
    app.mkdir()
    (app / "models.py").write_text("class User: pass\n", encoding="utf-8")
    (app / "service.py").write_text(
        "import os\n"
        "from .models import User\n"
        "import json as j\n",
        encoding="utf-8",
    )

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    service_imports = graph.get_imports_for("app/service.py")
    target_ids = {e.target_node_id for e in service_imports}

    # Relative internal import resolved to app/models.py
    assert "file:app/models.py" in target_ids

    # External stdlib modules resolved to module:os and module:json
    assert "module:os" in target_ids
    assert "module:json" in target_ids

    # Verify dependents helper
    deps = graph.get_dependents_of("app/models.py")
    assert any(d.id == "file:app/service.py" for d in deps)


def test_javascript_import_extraction(tmp_path: Path):
    """Test 6: JavaScript import extraction (ES6 imports, CommonJS require)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "math.js").write_text("export function add(a, b) { return a + b; }\n", encoding="utf-8")
    (src / "app.js").write_text(
        "import { add } from './math';\n"
        "const express = require('express');\n",
        encoding="utf-8",
    )

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    app_imports = graph.get_imports_for("src/app.js")
    target_ids = {e.target_node_id for e in app_imports}

    # Resolved internal file
    assert "file:src/math.js" in target_ids
    # External module
    assert "module:express" in target_ids


def test_typescript_import_extraction(tmp_path: Path):
    """Test 7: TypeScript import extraction (type imports, tsx resolution)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "types.ts").write_text("export interface User { id: string; }\n", encoding="utf-8")
    (src / "Button.tsx").write_text("export const Button = () => null;\n", encoding="utf-8")
    (src / "Dashboard.tsx").write_text(
        "import type { User } from './types';\n"
        "import { Button } from './Button';\n"
        "import React from 'react';\n",
        encoding="utf-8",
    )

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    dashboard_imports = graph.get_imports_for("src/Dashboard.tsx")
    target_ids = {e.target_node_id for e in dashboard_imports}

    assert "file:src/types.ts" in target_ids
    assert "file:src/Button.tsx" in target_ids
    assert "module:react" in target_ids


def test_unsupported_language_behavior(tmp_path: Path):
    """Test 8: Unsupported languages create FILE nodes without inventing relationships."""
    (tmp_path / "main.go").write_text("package main\nimport \"fmt\"\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "schema.sql").write_text("CREATE TABLE t (id INT);\n", encoding="utf-8")

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    go_node = graph.get_node("file:main.go")
    sql_node = graph.get_node("file:schema.sql")

    assert go_node is not None
    assert go_node.metadata.get("extraction_supported") is False
    assert go_node.metadata.get("language") == "go"

    assert sql_node is not None
    assert sql_node.metadata.get("extraction_supported") is False

    # No IMPORTS edges should be invented for unsupported files
    assert len(graph.get_imports_for("main.go")) == 0
    assert len(graph.get_imports_for("schema.sql")) == 0


def test_relationship_provenance(tmp_path: Path):
    """Test 9: Relationship provenance records exact line number, source file, and statement."""
    (tmp_path / "a.py").write_text("# Line 1\n# Line 2\nimport sys\n", encoding="utf-8")

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    imports = graph.get_imports_for("a.py")
    assert len(imports) == 1
    edge = imports[0]

    assert edge.evidence.source_file == "a.py"
    assert edge.evidence.line_number == 3
    assert edge.evidence.raw_statement == "import sys"
    assert edge.evidence.source_type == "ast"
    assert edge.evidence.confidence == "HIGH"


def test_stable_node_and_edge_identities(tmp_path: Path):
    """Tests 10, 11: Stable node and edge identities."""
    (tmp_path / "helper.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from .helper import x\n", encoding="utf-8")

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    res1 = scanner.scan()
    node_ids_1 = sorted(res1.graph.nodes.keys())
    edge_ids_1 = sorted(e.id for e in res1.graph.edges)

    # Re-scan in a separate scanner instance
    scanner2 = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    res2 = scanner2.scan()
    node_ids_2 = sorted(res2.graph.nodes.keys())
    edge_ids_2 = sorted(e.id for e in res2.graph.edges)

    assert node_ids_1 == node_ids_2
    assert edge_ids_1 == edge_ids_2


def test_repeated_graph_build_idempotence(tmp_path: Path):
    """Test 12: Repeated graph build produces equivalent graph and DB state."""
    db_file = tmp_path / ".buildcoach" / "state.db"
    (tmp_path / "app.py").write_text("import os\nimport sys\n", encoding="utf-8")

    scanner = ProjectScanner(tmp_path, db_path=db_file)
    res1 = scanner.scan()

    db = Database(db_file)
    db_graph1 = db.get_graph(res1.project.id)
    assert db_graph1 is not None

    res2 = scanner.scan()
    db_graph2 = db.get_graph(res2.project.id)
    assert db_graph2 is not None

    assert len(db_graph1.nodes) == len(db_graph2.nodes)
    assert len(db_graph1.edges) == len(db_graph2.edges)
    assert sorted(db_graph1.nodes.keys()) == sorted(db_graph2.nodes.keys())
    assert sorted(e.id for e in db_graph1.edges) == sorted(e.id for e in db_graph2.edges)


def test_graph_synchronization_lifecycle(tmp_path: Path):
    """Tests 13, 14, 15: Added file, changed relationships, and deleted file synchronization."""
    db_file = tmp_path / ".buildcoach" / "state.db"
    scanner = ProjectScanner(tmp_path, db_path=db_file)

    f1 = tmp_path / "f1.py"
    f1.write_text("import math\n", encoding="utf-8")

    # Initial scan
    scanner.scan()
    db = Database(db_file)
    g1 = db.get_graph(scanner.scan().project.id)
    assert "file:f1.py" in g1.nodes
    assert any(e.target_node_id == "module:math" for e in g1.edges)

    # 13. Added file synchronization
    f2 = tmp_path / "f2.py"
    f2.write_text("import f1\n", encoding="utf-8")
    scanner.scan()

    g2 = db.get_graph(g1.project_id)
    assert "file:f2.py" in g2.nodes
    assert any(e.source_node_id == "file:f2.py" and e.target_node_id == "file:f1.py" for e in g2.edges)

    # 15. Changed relationship synchronization (f2 now imports json instead of f1)
    f2.write_text("import json\n", encoding="utf-8")
    scanner.scan()

    g3 = db.get_graph(g1.project_id)
    f2_imports = [e for e in g3.edges if e.source_node_id == "file:f2.py" and e.edge_type == EdgeType.IMPORTS]
    target_ids_g3 = {e.target_node_id for e in f2_imports}
    assert "module:json" in target_ids_g3
    assert "file:f1.py" not in target_ids_g3

    # 14. Deleted file synchronization (f1 is deleted)
    f1.unlink()
    scanner.scan()

    g4 = db.get_graph(g1.project_id)
    assert "file:f1.py" not in g4.nodes
    # Stale edges to/from f1 must be gone
    assert not any(e.source_node_id == "file:f1.py" or e.target_node_id == "file:f1.py" for e in g4.edges)


def test_malformed_source_file_handling(tmp_path: Path):
    """Test 16: Malformed source code does not crash scanner or graph builder."""
    (tmp_path / "syntax_error.py").write_text("def broken(:\n   return None\n", encoding="utf-8")
    (tmp_path / "valid.py").write_text("import os\n", encoding="utf-8")

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    broken_node = graph.get_node("file:syntax_error.py")
    assert broken_node is not None
    assert "ParseError" in broken_node.metadata.get("extraction_error", "")

    # Valid file still extracted
    assert len(graph.get_imports_for("valid.py")) == 1


def test_binary_file_handling(tmp_path: Path):
    """Test 17: Binary file is safely indexed without content parsing."""
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    img_node = graph.get_node("file:image.png")
    assert img_node is not None
    assert img_node.metadata.get("is_binary") is True
    assert img_node.metadata.get("extraction_supported") is False
    assert len(graph.get_imports_for("image.png")) == 0


def test_path_boundary_and_security_behavior(tmp_path: Path):
    """Test 18: Path boundary safety; no code execution."""
    outside_file = tmp_path.parent / "outside.py"
    outside_file.write_text("import dangerous_lib\n", encoding="utf-8")

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    # Outside file must never be in graph nodes
    assert not any("outside.py" in n.id for n in graph.nodes.values())


def test_empty_project_behavior(tmp_path: Path):
    """Test 19: Empty project contains only project node with 0 edges."""
    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    summary = graph.summary()
    assert summary["total_nodes"] == 1
    assert summary["total_edges"] == 0
    assert summary["node_types"][NodeType.PROJECT] == 1


def test_mixed_language_project_behavior(tmp_path: Path):
    """Test 20: Mixed-language repository (Python backend + TS frontend + config)."""
    (tmp_path / "backend" / "api.py").parent.mkdir(parents=True)
    (tmp_path / "backend" / "api.py").write_text("import fastapi\n", encoding="utf-8")

    (tmp_path / "frontend" / "src" / "App.tsx").parent.mkdir(parents=True)
    (tmp_path / "frontend" / "src" / "App.tsx").write_text("import React from 'react';\n", encoding="utf-8")

    (tmp_path / "package.json").write_text('{"name": "root"}\n', encoding="utf-8")

    scanner = ProjectScanner(tmp_path, db_path=tmp_path / ".buildcoach" / "state.db")
    result = scanner.scan()
    graph = result.graph
    assert graph is not None

    assert "file:backend/api.py" in graph.nodes
    assert "file:frontend/src/App.tsx" in graph.nodes
    assert "file:package.json" in graph.nodes

    # Check edges
    api_imports = {e.target_node_id for e in graph.get_imports_for("backend/api.py")}
    assert "module:fastapi" in api_imports

    app_imports = {e.target_node_id for e in graph.get_imports_for("frontend/src/App.tsx")}
    assert "module:react" in app_imports
