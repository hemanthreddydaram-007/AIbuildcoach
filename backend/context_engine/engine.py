"""Context Engine implementation for AI Build Coach."""

import hashlib
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from backend.domain.models import (
    ContextRequest,
    ContextPacket,
    ContextItem,
    ContextPurpose,
    ContextSourceType,
    ChangeSet,
    ProjectGraph,
)
from backend.context_engine.secrets import detect_and_redact
from backend.context_engine.relevance import extract_relevance_candidates
from backend.context_engine.compression import (
    deduplicate_and_compress,
    apply_budget,
    estimate_tokens,
    DEFAULT_BUDGET_TOKENS,
)


def compute_item_id(
    source_type: str,
    source_reference: str,
    file_path: Optional[str] = None,
    line_start: Optional[int] = None,
    line_end: Optional[int] = None,
) -> str:
    """Generates a stable, deterministic item identifier."""
    raw = f"{source_type}|{source_reference}|{file_path or ''}|{line_start or ''}|{line_end or ''}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"ci_{digest}"


def compute_packet_id(
    project_id: str,
    purpose: str,
    items: List[ContextItem],
    change_set_id: Optional[str] = None,
) -> str:
    """Generates a deterministic packet ID based on project, purpose, and ordered items."""
    item_sigs = [f"{i.item_id}:{hashlib.sha256(i.content.encode('utf-8')).hexdigest()[:8]}" for i in items]
    raw = f"{project_id}|{purpose}|{change_set_id or ''}|{'|'.join(item_sigs)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"cp_{digest}"


def compute_cache_key(
    project_id: str,
    purpose: str,
    change_set_id: Optional[str] = None,
    graph_signature: Optional[str] = None,
    target_files: Optional[List[str]] = None,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
) -> str:
    """Computes a deterministic cache key for a context request."""
    targets = sorted(target_files or [])
    raw = f"{project_id}|{change_set_id or 'none'}|{graph_signature or 'none'}|{purpose}|{','.join(targets)}|{budget_tokens}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_source_type_order_rank(source_type: str, relevance_reason: str) -> int:
    """Determines canonical ordering rank for context items.
    
    Order:
    1. current change summary
    2. changed files
    3. changed hunks
    4. direct dependencies/dependents
    5. relevant tests
    6. relevant evidence
    7. explicitly requested context
    8. lower-priority supporting context
    """
    if source_type == ContextSourceType.CHANGESET and "summary" in relevance_reason.lower():
        return 1
    if source_type == ContextSourceType.CHANGESET:
        return 2
    if source_type == ContextSourceType.DIFF:
        return 3
    if source_type == ContextSourceType.PROJECT_GRAPH:
        return 4
    if source_type == ContextSourceType.TEST:
        return 5
    if source_type == ContextSourceType.EVIDENCE or source_type == ContextSourceType.GIT:
        return 6
    if "requested" in relevance_reason.lower() or source_type == ContextSourceType.FILE:
        return 7
    return 8


class ContextEngine:
    """Deterministic Context Engine.
    
    Transforms raw project data into a structured, safe, provenance-preserving ContextPacket.
    Pipeline: RAW DATA -> NORMALIZE -> RELEVANCE FILTER -> SECRET DETECTION -> REDACTION -> COMPRESS -> CONTEXT PACKET.
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db

    def build_context_packet(
        self,
        request: ContextRequest,
        project_files_content: Optional[Dict[str, str]] = None,
        use_cache: bool = True,
    ) -> ContextPacket:
        """Executes the deterministic context transformation pipeline."""
        
        # 1. NORMALIZE
        normalized_request = self._normalize_request(request)
        files_content = project_files_content or {}
        
        # Check cache if DB is available and caching requested
        cache_key = None
        if use_cache and self.db is not None:
            cs_id = normalized_request.change_set.id if normalized_request.change_set else None
            graph_sig = None
            if normalized_request.graph:
                graph_sig = f"nodes:{len(normalized_request.graph.nodes)}_edges:{len(normalized_request.graph.edges)}"
            cache_key = compute_cache_key(
                project_id=normalized_request.project_id,
                purpose=normalized_request.purpose,
                change_set_id=cs_id,
                graph_signature=graph_sig,
                target_files=normalized_request.target_files,
                budget_tokens=normalized_request.budget_tokens,
            )
            cached_packet = self.db.get_cached_context_packet(cache_key)
            if cached_packet is not None:
                return cached_packet

        # 2. RELEVANCE FILTER
        scored_candidates = extract_relevance_candidates(normalized_request, files_content)

        # 3. SECRET DETECTION & 4. REDACTION
        processed_items: List[ContextItem] = []
        total_secrets_detected = 0
        categories_detected: Dict[str, int] = {}
        all_evidence_refs: Set[str] = set()

        for score, raw_item in scored_candidates:
            raw_content = raw_item["content"]
            redacted_content, was_redacted, summary = detect_and_redact(raw_content)

            if was_redacted:
                total_secrets_detected += summary["total_secrets_detected"]
                for cat, count in summary["categories"].items():
                    categories_detected[cat] = categories_detected.get(cat, 0) + count

            item_id = compute_item_id(
                source_type=raw_item["source_type"],
                source_reference=raw_item["source_reference"],
                file_path=raw_item.get("file_path"),
                line_start=raw_item.get("line_start"),
                line_end=raw_item.get("line_end"),
            )

            ev_refs = raw_item.get("evidence_refs", [])
            for ref in ev_refs:
                all_evidence_refs.add(ref)

            processed_items.append(
                ContextItem(
                    item_id=item_id,
                    source_type=raw_item["source_type"],
                    source_reference=raw_item["source_reference"],
                    file_path=raw_item.get("file_path"),
                    line_start=raw_item.get("line_start"),
                    line_end=raw_item.get("line_end"),
                    evidence_refs=ev_refs,
                    relevance_reason=raw_item["relevance_reason"],
                    relevance_score=score,
                    redacted=was_redacted,
                    content=redacted_content,
                )
            )

        # 5. COMPRESS & BUDGET
        # First deduplicate and whitespace-normalize
        compressed_items = deduplicate_and_compress(processed_items)

        # Sort candidate items by priority for budget selection
        compressed_items.sort(
            key=lambda item: (
                -item.relevance_score,
                get_source_type_order_rank(item.source_type, item.relevance_reason),
                item.file_path or "",
                item.line_start or 0,
                item.item_id,
            )
        )

        # Apply token budget
        budgeted_items, token_estimate, truncation_status = apply_budget(
            compressed_items,
            budget_tokens=normalized_request.budget_tokens,
        )

        # Final deterministic ordering of the selected packet items
        budgeted_items.sort(
            key=lambda item: (
                get_source_type_order_rank(item.source_type, item.relevance_reason),
                -item.relevance_score,
                item.file_path or "",
                item.line_start or 0,
                item.item_id,
            )
        )

        # 6. CONTEXT PACKET ASSEMBLY
        cs_id = normalized_request.change_set.id if normalized_request.change_set else None
        packet_id = compute_packet_id(
            project_id=normalized_request.project_id,
            purpose=normalized_request.purpose,
            items=budgeted_items,
            change_set_id=cs_id,
        )

        redaction_summary = {
            "total_secrets_detected": total_secrets_detected,
            "categories": categories_detected,
        }

        packet = ContextPacket(
            id=packet_id,
            project_id=normalized_request.project_id,
            purpose=normalized_request.purpose,
            packet_version="1.0.0",
            items=budgeted_items,
            evidence_refs=sorted(all_evidence_refs),
            redaction_summary=redaction_summary,
            token_estimate=token_estimate,
            truncation_status=truncation_status,
        )

        # Persist and cache if DB available
        if self.db is not None:
            self.db.save_context_packet(packet=packet, request=normalized_request, cache_key=cache_key)

        return packet

    def _normalize_request(self, request: ContextRequest) -> ContextRequest:
        """Normalizes file paths and request parameters."""
        normalized_targets = []
        for tf in request.target_files:
            clean = tf.strip().replace("\\", "/").lstrip("/")
            if clean:
                normalized_targets.append(clean)
        
        budget = request.budget_tokens if request.budget_tokens > 0 else DEFAULT_BUDGET_TOKENS

        return ContextRequest(
            project_id=request.project_id.strip(),
            change_set=request.change_set,
            graph=request.graph,
            purpose=request.purpose,
            target_files=sorted(set(normalized_targets)),
            target_symbols=request.target_symbols,
            budget_tokens=budget,
        )
