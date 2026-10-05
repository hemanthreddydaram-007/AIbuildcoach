"""Conversation Evidence Service for deterministic claim and evidence grounding."""

import hashlib
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple

from backend.domain.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationClaim,
    EvidenceLink,
    ConversationEvidenceResult,
    ClaimStatus,
    EvidenceRelation,
    Project,
    ProjectFile,
    ProjectGraph,
    ChangeSet,
    FileChange,
    ChangeType,
    EvidenceRecord,
)
from backend.project_model.db import Database
from backend.context_engine.secrets import detect_and_redact


def deterministic_id(prefix: str, *parts: str) -> str:
    """Produces a deterministic ID based on SHA-256 hash of inputs."""
    combined = ":".join(str(p) for p in parts)
    h = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{h}"


# Common source code and configuration extensions
KNOWN_EXTENSIONS = {
    "py", "ts", "tsx", "js", "jsx", "java", "json", "toml", "yaml", "yml",
    "md", "css", "html", "sql", "sh", "txt", "rs", "go", "cpp", "c", "h",
    "xml", "gradle", "properties", "lock", "env",
}


def extract_candidate_paths(content: str) -> List[str]:
    """Extracts explicit, conservative file path references from message text.
    
    Untrusted input is parsed purely for file-path like tokens.
    """
    candidates: List[str] = []
    seen: Set[str] = set()

    def add_candidate(raw: str) -> None:
        p = raw.strip().replace("\\", "/")
        # Strip wrapping quotes or brackets
        p = re.sub(r"^['\"`\(\[\{]+", "", p)
        p = re.sub(r"['\"`\)\]\},;:!?\.]+$", "", p)
        if not p or len(p) < 2 or " " in p:
            return
        # Ignore web URLs
        if p.startswith("http://") or p.startswith("https://"):
            return
        # Ignore pure version numbers (e.g. 1.0, 2.1.0)
        if re.match(r"^v?\d+(\.\d+)+$", p):
            return

        # Check if it has a file extension or path separators
        has_slash = "/" in p
        ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
        if has_slash or ext in KNOWN_EXTENSIONS:
            if p not in seen:
                seen.add(p)
                candidates.append(p)

    # 1. Backtick code spans
    for match in re.finditer(r"`([^`\n]+)`", content):
        token = match.group(1).strip()
        add_candidate(token)

    # 2. Path-like tokens in plain text
    words = re.findall(r"(?:^|\s|[(\[\"'])([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]{1,10})(?:$|\s|[)\]\"',;:!?])", content)
    for w in words:
        add_candidate(w)

    return candidates


def is_path_traversal_or_outside(path_str: str, project_root: Path) -> bool:
    """Determines if a path string tries to escape or lies outside project root."""
    norm = path_str.replace("\\", "/")
    if norm.startswith("../") or "/../" in norm or norm == "..":
        return True
    # Absolute paths
    if norm.startswith("/") or re.match(r"^[a-zA-Z]:", norm):
        try:
            resolved = Path(path_str).resolve()
            root_resolved = project_root.resolve()
            return not (resolved == root_resolved or root_resolved in resolved.parents)
        except Exception:
            return True
    return False


class ConversationEvidenceService:
    """Service to connect normalized conversations to project evidence."""

    def __init__(self, db: Database):
        self.db = db

    def analyze_conversation(
        self,
        conversation_id: str,
        project_id: str,
    ) -> ConversationEvidenceResult:
        """Analyzes a conversation against project evidence deterministically."""
        # 1. Load conversation
        conversation = self.db.get_conversation(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")

        # 2. Load project
        project = self.db.get_project_by_id(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")

        project_root = Path(project.root_path)

        # 3. Load project state & evidence
        project_files = self.db.get_files_for_project(project_id)
        files_by_path: Dict[str, ProjectFile] = {f.path: f for f in project_files}
        files_by_name: Dict[str, List[str]] = {}
        for f in project_files:
            name = Path(f.path).name
            files_by_name.setdefault(name, []).append(f.path)

        graph = self.db.get_graph(project_id)
        graph_nodes = graph.nodes if graph else {}

        changeset = self.db.get_latest_change_set(project_id)
        file_changes = changeset.file_changes if changeset else []
        changes_by_new_path: Dict[str, FileChange] = {fc.new_path: fc for fc in file_changes if fc.new_path}
        changes_by_old_path: Dict[str, FileChange] = {fc.old_path: fc for fc in file_changes if fc.old_path}

        evidence_records: List[EvidenceRecord] = changeset.evidence if changeset else []
        evidence_by_file: Dict[str, List[EvidenceRecord]] = {}
        for ev in evidence_records:
            if ev.file_path:
                norm_ev_path = ev.file_path.replace("\\", "/")
                evidence_by_file.setdefault(norm_ev_path, []).append(ev)

        # 4. Extract claims from assistant messages (or all messages if no assistant messages)
        target_messages = [m for m in conversation.messages if m.role == ConversationRole.ASSISTANT]
        if not target_messages:
            target_messages = conversation.messages

        claims: List[ConversationClaim] = []
        evidence_links: List[EvidenceLink] = []

        for msg in target_messages:
            redacted_content, _, _ = detect_and_redact(msg.content)
            candidates = extract_candidate_paths(msg.content)

            if not candidates:
                # General message without explicit file paths -> UNKNOWN claim
                claim_id = deterministic_id("claim", conversation_id, msg.message_id, "general")
                claim = ConversationClaim(
                    claim_id=claim_id,
                    conversation_id=conversation_id,
                    message_id=msg.message_id,
                    claim_text=redacted_content[:200].strip(),
                    claim_type="GENERAL_STATEMENT",
                    referenced_paths=[],
                    confidence="LOW",
                    status=ClaimStatus.UNKNOWN,
                )
                link_id = deterministic_id("link", claim_id, "none", EvidenceRelation.NO_EVIDENCE)
                link = EvidenceLink(
                    link_id=link_id,
                    claim_id=claim_id,
                    evidence_id="none",
                    evidence_type="NONE",
                    relation=EvidenceRelation.NO_EVIDENCE,
                    confidence="LOW",
                )
                claims.append(claim)
                evidence_links.append(link)
                continue

            # Group all candidate paths in this message into a single explicit claim
            claim_id = deterministic_id("claim", conversation_id, msg.message_id, ",".join(sorted(candidates)))
            claim = ConversationClaim(
                claim_id=claim_id,
                conversation_id=conversation_id,
                message_id=msg.message_id,
                claim_text=redacted_content[:300].strip(),
                claim_type="FILE_REFERENCE",
                referenced_paths=candidates,
                confidence="HIGH",
                status=ClaimStatus.UNVERIFIED,
            )

            # Evaluate each path candidate
            path_evaluations: List[Dict[str, Any]] = []

            for raw_path in candidates:
                norm_path = raw_path.replace("\\", "/")
                # Check 1: Is path outside project?
                if is_path_traversal_or_outside(raw_path, project_root):
                    link_id = deterministic_id("link", claim_id, f"outside:{norm_path}", EvidenceRelation.CONTRADICTS)
                    evidence_links.append(
                        EvidenceLink(
                            link_id=link_id,
                            claim_id=claim_id,
                            evidence_id=f"outside:{norm_path}",
                            evidence_type="OUTSIDE_PROJECT",
                            relation=EvidenceRelation.CONTRADICTS,
                            confidence="HIGH",
                        )
                    )
                    path_evaluations.append({"path": norm_path, "status": "OUTSIDE"})
                    continue

                # Resolve relative path against project files if needed
                resolved_path = norm_path
                if resolved_path not in files_by_path and "/" not in resolved_path:
                    # Single filename; see if uniquely matched in project files
                    matches = files_by_name.get(resolved_path, [])
                    if len(matches) == 1:
                        resolved_path = matches[0]

                # Check 2: Does file exist?
                file_exists = resolved_path in files_by_path or (project_root / resolved_path).is_file()
                
                # Check if deleted in changeset
                deleted_change = (
                    changes_by_new_path.get(resolved_path) or changes_by_old_path.get(resolved_path)
                )
                is_deleted = deleted_change and deleted_change.change_type == ChangeType.DELETED

                if is_deleted:
                    link_id = deterministic_id("link", claim_id, deleted_change.id, EvidenceRelation.CONTRADICTS)
                    evidence_links.append(
                        EvidenceLink(
                            link_id=link_id,
                            claim_id=claim_id,
                            evidence_id=deleted_change.id,
                            evidence_type="FILE_CHANGE",
                            relation=EvidenceRelation.CONTRADICTS,
                            confidence="HIGH",
                        )
                    )
                    path_evaluations.append({"path": resolved_path, "status": "DELETED"})
                    continue

                if not file_exists:
                    # Nonexistent file
                    link_id = deterministic_id("link", claim_id, f"missing:{resolved_path}", EvidenceRelation.NO_EVIDENCE)
                    evidence_links.append(
                        EvidenceLink(
                            link_id=link_id,
                            claim_id=claim_id,
                            evidence_id=f"missing:{resolved_path}",
                            evidence_type="MISSING_FILE",
                            relation=EvidenceRelation.NO_EVIDENCE,
                            confidence="HIGH",
                        )
                    )
                    path_evaluations.append({"path": resolved_path, "status": "MISSING"})
                    continue

                # File exists! Link existence evidence
                link_id = deterministic_id("link", claim_id, f"file:{resolved_path}", EvidenceRelation.SUPPORTS)
                evidence_links.append(
                    EvidenceLink(
                        link_id=link_id,
                        claim_id=claim_id,
                        evidence_id=f"file:{resolved_path}",
                        evidence_type="FILE_EXISTENCE",
                        relation=EvidenceRelation.SUPPORTS,
                        confidence="HIGH",
                    )
                )

                # Check 3: ProjectGraph knowledge
                graph_node_id = f"file:{resolved_path}"
                if graph_node_id in graph_nodes:
                    node = graph_nodes[graph_node_id]
                    link_id = deterministic_id("link", claim_id, node.id, EvidenceRelation.SUPPORTS)
                    evidence_links.append(
                        EvidenceLink(
                            link_id=link_id,
                            claim_id=claim_id,
                            evidence_id=node.id,
                            evidence_type="GRAPH_NODE",
                            relation=EvidenceRelation.SUPPORTS,
                            confidence="HIGH",
                        )
                    )

                # Check 4: Was it changed?
                fc = changes_by_new_path.get(resolved_path) or changes_by_old_path.get(resolved_path)
                was_changed = fc is not None and fc.change_type != ChangeType.DELETED

                if was_changed:
                    link_id = deterministic_id("link", claim_id, fc.id, EvidenceRelation.SUPPORTS)
                    evidence_links.append(
                        EvidenceLink(
                            link_id=link_id,
                            claim_id=claim_id,
                            evidence_id=fc.id,
                            evidence_type="FILE_CHANGE",
                            relation=EvidenceRelation.SUPPORTS,
                            confidence="HIGH",
                        )
                    )
                else:
                    link_id = deterministic_id("link", claim_id, f"unchanged:{resolved_path}", EvidenceRelation.PARTIALLY_SUPPORTS)
                    evidence_links.append(
                        EvidenceLink(
                            link_id=link_id,
                            claim_id=claim_id,
                            evidence_id=f"unchanged:{resolved_path}",
                            evidence_type="FILE_STATUS",
                            relation=EvidenceRelation.PARTIALLY_SUPPORTS,
                            confidence="MEDIUM",
                        )
                    )

                # Check 5: Git / M3 evidence records
                for ev in evidence_by_file.get(resolved_path, []):
                    link_id = deterministic_id("link", claim_id, ev.id, EvidenceRelation.SUPPORTS)
                    evidence_links.append(
                        EvidenceLink(
                            link_id=link_id,
                            claim_id=claim_id,
                            evidence_id=ev.id,
                            evidence_type="GIT_EVIDENCE",
                            relation=EvidenceRelation.SUPPORTS,
                            confidence=ev.confidence,
                        )
                    )

                # Check 6: Associated test file changes (grounding rule)
                # If this is an implementation file, check if a corresponding test file exists
                has_test_file = False
                test_file_changed = False
                base_stem = Path(resolved_path).stem
                test_candidates = [
                    f"tests/test_{base_stem}.py",
                    f"test_{base_stem}.py",
                    f"tests/{base_stem}.test.ts",
                    f"{base_stem}.test.ts",
                    f"{base_stem}.spec.ts",
                ]
                for tc in test_candidates:
                    if tc in files_by_path or (project_root / tc).is_file():
                        has_test_file = True
                        if tc in changes_by_new_path or tc in changes_by_old_path:
                            test_file_changed = True
                        break

                path_evaluations.append({
                    "path": resolved_path,
                    "status": "EXISTS",
                    "changed": was_changed,
                    "has_test_file": has_test_file,
                    "test_file_changed": test_file_changed,
                })

            # Calculate deterministic claim status
            statuses = [e["status"] for e in path_evaluations]
            if any(s == "OUTSIDE" for s in statuses):
                claim.status = ClaimStatus.UNSUPPORTED
            elif all(s in ("MISSING", "DELETED") for s in statuses):
                claim.status = ClaimStatus.UNSUPPORTED
            elif any(s in ("MISSING", "DELETED") for s in statuses):
                claim.status = ClaimStatus.PARTIALLY_SUPPORTED
            else:
                # All files exist
                changed_flags = [e.get("changed", False) for e in path_evaluations]
                test_flags = [e.get("has_test_file", False) and not e.get("test_file_changed", False) for e in path_evaluations]

                if all(changed_flags):
                    # Check if implementation changed but test unobserved
                    if any(test_flags):
                        claim.status = ClaimStatus.PARTIALLY_SUPPORTED
                    else:
                        claim.status = ClaimStatus.SUPPORTED
                elif any(changed_flags):
                    # Some changed, some unchanged
                    claim.status = ClaimStatus.PARTIALLY_SUPPORTED
                else:
                    # None changed (unchanged files)
                    claim.status = ClaimStatus.PARTIALLY_SUPPORTED

            claims.append(claim)

        # 5. Build summary
        status_counts = {
            ClaimStatus.SUPPORTED: sum(1 for c in claims if c.status == ClaimStatus.SUPPORTED),
            ClaimStatus.PARTIALLY_SUPPORTED: sum(1 for c in claims if c.status == ClaimStatus.PARTIALLY_SUPPORTED),
            ClaimStatus.UNSUPPORTED: sum(1 for c in claims if c.status == ClaimStatus.UNSUPPORTED),
            ClaimStatus.UNKNOWN: sum(1 for c in claims if c.status == ClaimStatus.UNKNOWN),
            ClaimStatus.UNVERIFIED: sum(1 for c in claims if c.status == ClaimStatus.UNVERIFIED),
        }

        all_refs: List[str] = []
        for c in claims:
            all_refs.extend(c.referenced_paths)

        return ConversationEvidenceResult(
            conversation_id=conversation_id,
            project_id=project_id,
            claims=claims,
            evidence_links=evidence_links,
            summary={
                "total_claims": len(claims),
                "total_links": len(evidence_links),
                "status_counts": status_counts,
                "referenced_files": sorted(list(set(all_refs))),
            },
        )
