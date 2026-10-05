"""Domain models for AI Build Coach Foundation."""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
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


class ChangeType(str):
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    RENAMED = "RENAMED"


class DiffHunk(BaseModel):
    id: str
    file_change_id: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    header: Optional[str] = None
    content: str


class FileChange(BaseModel):
    id: str
    change_set_id: str
    old_path: Optional[str] = None
    new_path: str
    change_type: str  # ADDED, MODIFIED, DELETED, RENAMED
    is_staged: bool = False
    is_untracked: bool = False
    old_line_count: Optional[int] = None
    new_line_count: Optional[int] = None
    line_ranges: List[Tuple[int, int]] = Field(default_factory=list)
    hunks: List[DiffHunk] = Field(default_factory=list)
    is_binary: bool = False


class EvidenceRecord(BaseModel):
    id: str
    project_id: str
    change_set_id: Optional[str] = None
    evidence_type: str  # GIT_STATUS, GIT_DIFF, RECENT_COMMIT, WORKING_TREE
    source: str  # e.g. git status --porcelain, git diff, git diff --cached, git log -1
    file_path: Optional[str] = None
    observation: str
    raw_data: Optional[str] = None
    confidence: str = "HIGH"
    created_at: str = Field(default_factory=utc_now_iso)


class ChangeSet(BaseModel):
    id: str
    project_id: str
    git_state: GitState
    file_changes: List[FileChange] = Field(default_factory=list)
    evidence: List[EvidenceRecord] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class ScanResult(BaseModel):
    project: Project
    files: List[ProjectFile]
    git_state: GitState
    graph: Optional[ProjectGraph] = None
    change_set: Optional[ChangeSet] = None
    scanned_at: str = Field(default_factory=utc_now_iso)
    duration_ms: float = 0.0
    errors: List[str] = Field(default_factory=list)


class ContextPurpose(str):
    CHANGE_EXPLANATION = "CHANGE_EXPLANATION"
    FILE_UNDERSTANDING = "FILE_UNDERSTANDING"
    PROJECT_OVERVIEW = "PROJECT_OVERVIEW"
    DEPENDENCY_CONTEXT = "DEPENDENCY_CONTEXT"


class ContextSourceType(str):
    PROJECT_GRAPH = "PROJECT_GRAPH"
    CHANGESET = "CHANGESET"
    FILE = "FILE"
    DIFF = "DIFF"
    EVIDENCE = "EVIDENCE"
    TEST = "TEST"
    GIT = "GIT"


class ContextItem(BaseModel):
    item_id: str
    source_type: str
    source_reference: str
    file_path: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    evidence_refs: List[str] = Field(default_factory=list)
    relevance_reason: str
    relevance_score: float = 0.0
    redacted: bool = False
    content: str


class ContextRequest(BaseModel):
    project_id: str
    change_set: Optional[ChangeSet] = None
    graph: Optional[ProjectGraph] = None
    purpose: str = ContextPurpose.CHANGE_EXPLANATION
    target_files: List[str] = Field(default_factory=list)
    target_symbols: List[str] = Field(default_factory=list)
    budget_tokens: int = 4000


class ContextPacket(BaseModel):
    id: str
    project_id: str
    purpose: str
    generated_at: str = Field(default_factory=utc_now_iso)
    packet_version: str = "1.0.0"
    items: List[ContextItem] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    redaction_summary: Dict[str, Any] = Field(default_factory=dict)
    token_estimate: int = 0
    truncation_status: str = "NONE"  # "NONE", "TRUNCATED"


class ConversationRole(str):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class ConversationProvider(str):
    CHATGPT = "CHATGPT"
    CLAUDE = "CLAUDE"
    GEMINI = "GEMINI"
    OTHER = "OTHER"


class ConversationSource(str):
    PASTE = "PASTE"
    IMPORT = "IMPORT"
    FUTURE_EXTENSION = "FUTURE_EXTENSION"
    FUTURE_DESKTOP = "FUTURE_DESKTOP"


class ConversationMessage(BaseModel):
    message_id: str
    role: str  # USER, ASSISTANT, SYSTEM
    content: str
    timestamp: Optional[str] = None
    sequence: int
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Conversation(BaseModel):
    conversation_id: str
    provider: str  # CHATGPT, CLAUDE, GEMINI, OTHER
    source: str = ConversationSource.IMPORT
    project_id: Optional[str] = None
    title: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    messages: List[ConversationMessage] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationConsent(BaseModel):
    consent_id: str
    approved: bool
    granted_at: str = Field(default_factory=utc_now_iso)
    scope: str = "CONVERSATION_INGESTION"
    reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

