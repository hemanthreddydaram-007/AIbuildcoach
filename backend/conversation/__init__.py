"""Conversation Bridge Foundation for AI Build Coach."""

from backend.domain.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationProvider,
    ConversationSource,
    ConversationConsent,
    ConversationClaim,
    EvidenceLink,
    ConversationEvidenceResult,
    ClaimStatus,
    EvidenceRelation,
)
from backend.conversation.evidence_service import ConversationEvidenceService

__all__ = [
    "Conversation",
    "ConversationMessage",
    "ConversationRole",
    "ConversationProvider",
    "ConversationSource",
    "ConversationConsent",
    "ConversationClaim",
    "EvidenceLink",
    "ConversationEvidenceResult",
    "ClaimStatus",
    "EvidenceRelation",
    "ConversationEvidenceService",
]

