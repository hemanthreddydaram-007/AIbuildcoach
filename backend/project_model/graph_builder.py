"""Deterministic Project Graph Builder for AI Build Coach."""

from pathlib import Path
from typing import List, Dict, Optional, Set

from backend.domain.models import (
    Project,
    ProjectFile,
    ProjectGraph,
    GraphNode,
    GraphEdge,
    ProvenanceRecord,
    NodeType,
    EdgeType,
)
from backend.project_model.relationship_extractor import (
    extract_file_relationships,
    ExtractionResult,
    ExtractedImport,
)
from backend.project_model.db import Database


class ProjectGraphBuilder:
    def __init__(
        self,
        project_root: Path,
        project: Project,
        files: List[ProjectFile],
        db: Optional[Database] = None,
    ):
        self.project_root = project_root.resolve()
        self.project = project
        self.files = files
        self.db = db
        self.file_map: Dict[str, ProjectFile] = {f.path: f for f in files}

    def build(self) -> ProjectGraph:
        """Constructs a deterministic ProjectGraph from scanned files and extracts evidence-backed relationships."""
        graph = ProjectGraph(project_id=self.project.id)

        # 1. Create Root Project Node
        project_node_id = f"project:{self.project.id}"
        graph.add_node(
            GraphNode(
                id=project_node_id,
                project_id=self.project.id,
                node_type=NodeType.PROJECT,
                name=self.project.name,
                path="",
                metadata={"root_path": str(self.project_root)},
            )
        )

        # 2. Extract and create Directory Nodes & CONTAINS relationships
        dir_paths: Set[str] = set()
        for f in self.files:
            p = Path(f.path)
            # Collect all ancestor directory parts
            curr = p.parent
            while curr != Path(".") and str(curr) != "":
                dir_paths.add(curr.as_posix())
                curr = curr.parent

        sorted_dirs = sorted(dir_paths)
        for d in sorted_dirs:
            dir_node_id = f"dir:{d}"
            dir_name = Path(d).name
            graph.add_node(
                GraphNode(
                    id=dir_node_id,
                    project_id=self.project.id,
                    node_type=NodeType.DIRECTORY,
                    name=dir_name,
                    path=d,
                    metadata={},
                )
            )

            # Link directory to parent directory or project root
            parent_dir = Path(d).parent
            if parent_dir == Path(".") or str(parent_dir) == "":
                parent_id = project_node_id
            else:
                parent_id = f"dir:{parent_dir.as_posix()}"

            graph.add_edge(
                GraphEdge(
                    id=f"edge:{parent_id}->CONTAINS->{dir_node_id}",
                    project_id=self.project.id,
                    source_node_id=parent_id,
                    target_node_id=dir_node_id,
                    edge_type=EdgeType.CONTAINS,
                    evidence=ProvenanceRecord(
                        source_file=d,
                        line_number=None,
                        raw_statement=f"directory hierarchy: {d}",
                        source_type="filesystem",
                        confidence="HIGH",
                    ),
                )
            )

        # 3. Create File Nodes & CONTAINS relationships
        for f in self.files:
            file_node_id = f"file:{f.path}"
            file_path_obj = self.project_root / f.path

            # Extraction
            ext_res = extract_file_relationships(
                file_path=file_path_obj,
                rel_path=f.path,
                is_binary=f.is_binary,
                is_large=f.is_large,
            )

            metadata = {
                "language": ext_res.language,
                "extraction_supported": ext_res.is_supported,
                "file_size": f.file_size,
                "sha256": f.sha256_hash,
                "is_binary": f.is_binary,
                "is_large": f.is_large,
            }
            if ext_res.error:
                metadata["extraction_error"] = ext_res.error

            graph.add_node(
                GraphNode(
                    id=file_node_id,
                    project_id=self.project.id,
                    node_type=NodeType.FILE,
                    name=Path(f.path).name,
                    path=f.path,
                    metadata=metadata,
                )
            )

            # File CONTAINS edge from parent directory or project root
            p = Path(f.path)
            if p.parent == Path(".") or str(p.parent) == "":
                parent_id = project_node_id
            else:
                parent_id = f"dir:{p.parent.as_posix()}"

            graph.add_edge(
                GraphEdge(
                    id=f"edge:{parent_id}->CONTAINS->{file_node_id}",
                    project_id=self.project.id,
                    source_node_id=parent_id,
                    target_node_id=file_node_id,
                    edge_type=EdgeType.CONTAINS,
                    evidence=ProvenanceRecord(
                        source_file=f.path,
                        line_number=None,
                        raw_statement=f"filesystem hierarchy: {f.path}",
                        source_type="filesystem",
                        confidence="HIGH",
                    ),
                )
            )

            # 4. Extract and resolve IMPORTS relationships
            if ext_res.is_supported and ext_res.imports:
                for imp in ext_res.imports:
                    target_file = self._resolve_import_to_internal_file(f.path, imp)
                    if target_file:
                        target_id = f"file:{target_file}"
                    else:
                        # External module dependency
                        module_name = self._extract_external_module_name(imp)
                        target_id = f"module:{module_name}"
                        if target_id not in graph.nodes:
                            graph.add_node(
                                GraphNode(
                                    id=target_id,
                                    project_id=self.project.id,
                                    node_type=NodeType.MODULE,
                                    name=module_name,
                                    path=None,
                                    metadata={"is_external": True},
                                )
                            )

                    edge_id = f"edge:{file_node_id}->IMPORTS->{target_id}@{imp.line_number or 0}:{imp.imported_name}"
                    graph.add_edge(
                        GraphEdge(
                            id=edge_id,
                            project_id=self.project.id,
                            source_node_id=file_node_id,
                            target_node_id=target_id,
                            edge_type=EdgeType.IMPORTS,
                            evidence=ProvenanceRecord(
                                source_file=f.path,
                                line_number=imp.line_number,
                                raw_statement=imp.raw_statement,
                                source_type=imp.source_type,
                                confidence="HIGH",
                            ),
                        )
                    )

        # Sort edges deterministically
        graph.edges.sort(key=lambda e: (e.source_node_id, e.edge_type, e.target_node_id, e.id))

        # 5. Persist to database if provided
        if self.db:
            self.db.save_graph(graph)

        return graph

    def _resolve_import_to_internal_file(self, source_file: str, imp: ExtractedImport) -> Optional[str]:
        """Resolves an import to a project-internal relative file path if one exists."""
        source_dir = Path(source_file).parent

        # 1. Relative Python import (e.g. from .models import User or from ..db import Database)
        if imp.level > 0:
            target_base = source_dir
            for _ in range(imp.level - 1):
                target_base = target_base.parent

            candidates = []
            if imp.source_module:
                subpath = imp.source_module.replace(".", "/")
                candidates.append((target_base / f"{subpath}.py").as_posix())
                candidates.append((target_base / subpath / "__init__.py").as_posix())
            if imp.imported_name:
                candidates.append((target_base / f"{imp.imported_name}.py").as_posix())
                candidates.append((target_base / imp.imported_name / "__init__.py").as_posix())

            for c in candidates:
                c_clean = Path(c).as_posix()
                if c_clean in self.file_map:
                    return c_clean

        # 2. Relative JS/TS import (e.g. ./Button, ../utils/auth)
        if imp.imported_name.startswith("."):
            norm_base = (source_dir / imp.imported_name).as_posix()
            extensions = ["", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", "/index.js", "/index.ts", "/index.tsx", "/index.jsx"]
            for ext in extensions:
                cand = Path(f"{norm_base}{ext}").as_posix()
                if cand in self.file_map:
                    return cand

        # 3. Absolute Python import (e.g. backend.domain.models or from backend.domain.models import Project)
        candidates = []
        if imp.source_module:
            src_path = imp.source_module.replace(".", "/")
            candidates.append(f"{src_path}.py")
            candidates.append(f"{src_path}/__init__.py")
            # Combined module + item
            combined = f"{src_path}/{imp.imported_name.replace('.', '/')}"
            candidates.append(f"{combined}.py")
            candidates.append(f"{combined}/__init__.py")

        if imp.imported_name:
            imp_path = imp.imported_name.replace(".", "/")
            candidates.append(f"{imp_path}.py")
            candidates.append(f"{imp_path}/__init__.py")

        for c in candidates:
            c_clean = Path(c).as_posix()
            if c_clean in self.file_map:
                return c_clean

        return None

    def _extract_external_module_name(self, imp: ExtractedImport) -> str:
        """Determines the canonical external package name."""
        raw = imp.source_module or imp.imported_name
        if raw.startswith("@"):
            # Scoped package e.g. @org/pkg
            parts = raw.split("/")
            return "/".join(parts[:2])
        return raw.split(".")[0].split("/")[0]
