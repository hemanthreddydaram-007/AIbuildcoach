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
    VerificationVerdict,
    VerificationRequest,
    VerificationResult,
)
from backend.conversation.evidence_service import ConversationEvidenceService
from backend.conversation.verification_service import ConversationVerificationService

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
    "VerificationVerdict",
    "VerificationRequest",
    "VerificationResult",
    "ConversationEvidenceService",
    "ConversationVerificationService",
]


