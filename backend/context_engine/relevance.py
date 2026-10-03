"""Deterministic relevance scoring and candidate extraction for AI Build Coach."""

from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from backend.domain.models import (
    ContextRequest,
    ContextPurpose,
    ContextSourceType,
    ContextItem,
    ChangeSet,
    ProjectGraph,
    FileChange,
    DiffHunk,
    EvidenceRecord,
    EdgeType,
    NodeType,
)


# Explicit, documented relevance scores
SCORE_DIRECTLY_CHANGED_FILE = 100.0
SCORE_CHANGED_DIFF_HUNK = 95.0
SCORE_EXPLICITLY_REQUESTED = 90.0
SCORE_DIRECT_DEPENDENCY = 70.0
SCORE_DIRECT_DEPENDENT = 60.0
SCORE_RELATED_TEST = 50.0
SCORE_RELATED_CONFIG = 40.0
SCORE_CHANGE_EVIDENCE = 30.0
SCORE_SUPPORTING_GRAPH = 20.0
SCORE_UNRELATED_FILE = 0.0

KNOWN_CONFIG_NAMES = {
    "pyproject.toml",
    "package.json",
    "tsconfig.json",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "cargo.toml",
    "go.mod",
    "dockerfile",
    "makefile",
}


def is_test_file(path_str: str) -> bool:
    """Checks if a file path represents a test file."""
    p = Path(path_str)
    parts = [part.lower() for part in p.parts]
    if "tests" in parts or "test" in parts:
        return True
    name = p.name.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.js")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.js")
    )


def is_related_test(test_path: str, changed_paths: Set[str]) -> bool:
    """Checks if a test file specifically tests any of the changed paths."""
    test_p = Path(test_path)
    test_stem = test_p.stem.lower()
    
    # Strip test prefixes/suffixes
    cleaned_test_stem = test_stem
    if cleaned_test_stem.startswith("test_"):
        cleaned_test_stem = cleaned_test_stem[5:]
    if cleaned_test_stem.endswith("_test"):
        cleaned_test_stem = cleaned_test_stem[:-5]
    if cleaned_test_stem.endswith(".test"):
        cleaned_test_stem = cleaned_test_stem[:-5]
    if cleaned_test_stem.endswith(".spec"):
        cleaned_test_stem = cleaned_test_stem[:-5]

    for cp in changed_paths:
        target_stem = Path(cp).stem.lower()
        if target_stem == cleaned_test_stem:
            return True
        if target_stem in cleaned_test_stem or cleaned_test_stem in target_stem:
            return True
    return False


def extract_relevance_candidates(
    request: ContextRequest,
    project_files_content: Optional[Dict[str, str]] = None,
) -> List[Tuple[float, Dict]]:
    """Extracts candidate context items with deterministic relevance scores and provenance.
    
    Returns a list of tuples: (relevance_score, item_dict)
    Candidates with score <= 0.0 are filtered out.
    """
    candidates: List[Tuple[float, Dict]] = []
    files_content = project_files_content or {}

    # Set of changed file paths
    changed_file_paths: Set[str] = set()
    if request.change_set:
        for fc in request.change_set.file_changes:
            changed_file_paths.add(fc.new_path)
            if fc.old_path:
                changed_file_paths.add(fc.old_path)

    # Explicit target files
    explicit_targets = set(request.target_files)

    # 1. Directly changed files from ChangeSet
    if request.change_set:
        for fc in sorted(request.change_set.file_changes, key=lambda f: f.new_path):
            file_path = fc.new_path
            content = files_content.get(file_path, f"File {file_path} changed ({fc.change_type})")
            
            # Associate evidence refs
            ev_refs = [
                ev.id for ev in request.change_set.evidence
                if ev.file_path == file_path or ev.file_path is None
            ]

            candidates.append((
                SCORE_DIRECTLY_CHANGED_FILE,
                {
                    "source_type": ContextSourceType.CHANGESET,
                    "source_reference": fc.id,
                    "file_path": file_path,
                    "line_start": None,
                    "line_end": None,
                    "evidence_refs": sorted(ev_refs),
                    "relevance_reason": f"directly changed file ({fc.change_type.lower()})",
                    "content": content,
                }
            ))

            # 2. Diff hunks for this changed file
            for hunk in fc.hunks:
                candidates.append((
                    SCORE_CHANGED_DIFF_HUNK,
                    {
                        "source_type": ContextSourceType.DIFF,
                        "source_reference": hunk.id,
                        "file_path": file_path,
                        "line_start": hunk.new_start,
                        "line_end": hunk.new_start + max(0, hunk.new_lines - 1),
                        "evidence_refs": sorted(ev_refs),
                        "relevance_reason": "changed diff hunk",
                        "content": hunk.content,
                    }
                ))

    # 3. Explicitly requested target files
    for tf in sorted(explicit_targets):
        if tf not in changed_file_paths:
            content = files_content.get(tf, f"Target file {tf}")
            candidates.append((
                SCORE_EXPLICITLY_REQUESTED,
                {
                    "source_type": ContextSourceType.FILE,
                    "source_reference": f"target:{tf}",
                    "file_path": tf,
                    "line_start": None,
                    "line_end": None,
                    "evidence_refs": [],
                    "relevance_reason": "explicitly requested target file",
                    "content": content,
                }
            ))

    # 4. Project Graph relationships (dependencies, dependents, test nodes)
    graph = request.graph
    direct_dependencies: Set[str] = set()
    direct_dependents: Set[str] = set()

    if graph:
        active_paths = changed_file_paths or explicit_targets
        for path in sorted(active_paths):
            file_node_id = f"file:{path}"
            
            # Dependencies (files imported by active path)
            for edge in graph.get_out_edges(file_node_id, EdgeType.IMPORTS):
                target_node = graph.get_node(edge.target_node_id)
                if target_node and target_node.path and target_node.path not in active_paths:
                    dep_path = target_node.path
                    direct_dependencies.add(dep_path)
                    content = files_content.get(dep_path, f"Dependency {dep_path}")
                    candidates.append((
                        SCORE_DIRECT_DEPENDENCY,
                        {
                            "source_type": ContextSourceType.PROJECT_GRAPH,
                            "source_reference": edge.id,
                            "file_path": dep_path,
                            "line_start": edge.evidence.line_number if edge.evidence else None,
                            "line_end": edge.evidence.line_number if edge.evidence else None,
                            "evidence_refs": [],
                            "relevance_reason": f"direct dependency imported by {path}",
                            "content": content,
                        }
                    ))

            # Dependents (files importing active path)
            for edge in graph.get_in_edges(file_node_id, EdgeType.IMPORTS):
                source_node = graph.get_node(edge.source_node_id)
                if source_node and source_node.path and source_node.path not in active_paths:
                    dep_path = source_node.path
                    direct_dependents.add(dep_path)
                    content = files_content.get(dep_path, f"Dependent {dep_path}")
                    candidates.append((
                        SCORE_DIRECT_DEPENDENT,
                        {
                            "source_type": ContextSourceType.PROJECT_GRAPH,
                            "source_reference": edge.id,
                            "file_path": dep_path,
                            "line_start": edge.evidence.line_number if edge.evidence else None,
                            "line_end": edge.evidence.line_number if edge.evidence else None,
                            "evidence_refs": [],
                            "relevance_reason": f"direct dependent importing {path}",
                            "content": content,
                        }
                    ))

    # 5. Related test files
    active_paths = changed_file_paths or explicit_targets
    for path, content in sorted(files_content.items()):
        if is_test_file(path) and path not in active_paths and path not in direct_dependencies and path not in direct_dependents:
            if is_related_test(path, active_paths):
                candidates.append((
                    SCORE_RELATED_TEST,
                    {
                        "source_type": ContextSourceType.TEST,
                        "source_reference": f"test:{path}",
                        "file_path": path,
                        "line_start": None,
                        "line_end": None,
                        "evidence_refs": [],
                        "relevance_reason": f"related test file for changed code",
                        "content": content,
                    }
                ))

    # 6. Relevant configuration files
    for path, content in sorted(files_content.items()):
        p_lower = Path(path).name.lower()
        if p_lower in KNOWN_CONFIG_NAMES and path not in active_paths and path not in direct_dependencies:
            candidates.append((
                SCORE_RELATED_CONFIG,
                {
                    "source_type": ContextSourceType.FILE,
                    "source_reference": f"config:{path}",
                    "file_path": path,
                    "line_start": None,
                    "line_end": None,
                    "evidence_refs": [],
                    "relevance_reason": "project configuration",
                    "content": content,
                }
            ))

    # 7. Git / Change Evidence records
    if request.change_set and request.change_set.evidence:
        for ev in sorted(request.change_set.evidence, key=lambda e: e.id):
            candidates.append((
                SCORE_CHANGE_EVIDENCE,
                {
                    "source_type": ContextSourceType.EVIDENCE,
                    "source_reference": ev.id,
                    "file_path": ev.file_path,
                    "line_start": None,
                    "line_end": None,
                    "evidence_refs": [ev.id],
                    "relevance_reason": f"deterministic evidence ({ev.evidence_type.lower()})",
                    "content": f"Observation: {ev.observation}\nSource: {ev.source}\n{ev.raw_data or ''}".strip(),
                }
            ))

    # Filter out any candidates with score <= 0.0 (unrelated files)
    return [(score, item) for score, item in candidates if score > SCORE_UNRELATED_FILE]
