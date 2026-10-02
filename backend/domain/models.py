"""Domain models for AI Build Coach Foundation."""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NodeType(str):
    PROJECT = "PROJECT"
    DIRECTORY = "DIRECTORY"
    FILE = "FILE"
    MODULE = "MODULE"


class EdgeType(str):
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    REFERENCES = "REFERENCES"


class ProvenanceRecord(BaseModel):
    source_file: str
    line_number: Optional[int] = None
    raw_statement: Optional[str] = None
    source_type: str = "ast"  # "ast", "regex", "filesystem"
    confidence: str = "HIGH"  # "HIGH", "MEDIUM"


class GraphNode(BaseModel):
    id: str
    project_id: str
    node_type: str
    name: str
    path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class GraphEdge(BaseModel):
    id: str
    project_id: str
    source_node_id: str
    target_node_id: str
    edge_type: str
    evidence: ProvenanceRecord
    status: str = "CONFIRMED"
    created_at: str = Field(default_factory=utc_now_iso)


class ProjectGraph(BaseModel):
    project_id: str
    nodes: Dict[str, GraphNode] = Field(default_factory=dict)
    edges: list[GraphEdge] = Field(default_factory=list)

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def get_out_edges(self, source_node_id: str, edge_type: Optional[str] = None) -> list[GraphEdge]:
        return [
            e for e in self.edges
            if e.source_node_id == source_node_id and (edge_type is None or e.edge_type == edge_type)
        ]

    def get_in_edges(self, target_node_id: str, edge_type: Optional[str] = None) -> list[GraphEdge]:
        return [
            e for e in self.edges
            if e.target_node_id == target_node_id and (edge_type is None or e.edge_type == edge_type)
        ]

    def get_imports_for(self, file_path: str) -> list[GraphEdge]:
        file_node_id = f"file:{file_path}"
        return self.get_out_edges(file_node_id, EdgeType.IMPORTS)

    def get_dependents_of(self, file_path: str) -> list[GraphNode]:
        file_node_id = f"file:{file_path}"
        in_edges = self.get_in_edges(file_node_id, EdgeType.IMPORTS)
        source_ids = {e.source_node_id for e in in_edges}
        return [self.nodes[sid] for sid in source_ids if sid in self.nodes]

    def summary(self) -> Dict[str, Any]:
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "node_types": {
                t: len([n for n in self.nodes.values() if n.node_type == t])
                for t in (NodeType.PROJECT, NodeType.DIRECTORY, NodeType.FILE, NodeType.MODULE)
            },
            "edge_types": {
                t: len([e for e in self.edges if e.edge_type == t])
                for t in (EdgeType.CONTAINS, EdgeType.IMPORTS, EdgeType.REFERENCES)
            },
        }


class SchemaVersion(BaseModel):
    version: int
    applied_at: str = Field(default_factory=utc_now_iso)
    description: str


class GitState(BaseModel):
    is_git_repo: bool = False
    current_branch: Optional[str] = None
    head_commit: Optional[str] = None
    is_dirty: bool = False
    untracked_count: int = 0
    modified_count: int = 0
    staged_count: int = 0


class Project(BaseModel):
    id: str
    name: str
    root_path: str
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class ProjectFile(BaseModel):
    path: str  # Normalized relative path with forward slashes
    absolute_path: str
    file_size: int
    last_modified: float
    sha256_hash: str
    file_type: str
    is_binary: bool = False
    is_large: bool = False
    is_ignored: bool = False


class ProjectSummary(BaseModel):
    project_id: str
    total_files: int
    total_size_bytes: int
    file_types: Dict[str, int] = Field(default_factory=dict)
    git_state: GitState
    last_scanned: str = Field(default_factory=utc_now_iso)


class ScanResult(BaseModel):
    project: Project
    files: list[ProjectFile]
    git_state: GitState
    graph: Optional[ProjectGraph] = None
    scanned_at: str = Field(default_factory=utc_now_iso)
    duration_ms: float = 0.0
    errors: list[str] = Field(default_factory=list)
