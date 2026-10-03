"""AI Gateway module for AI Build Coach."""

from backend.ai_gateway.models import (
    ClaimType,
    StructuredClaim,
    ExplanationResponse,
    ConsentToken,
    TransmissionPreview,
    RawInteractionResponse,
    ValidatedGatewayResult,
)
from backend.ai_gateway.exceptions import (
    AIGatewayError,
    ConsentViolationError,
    CredentialMissingError,
    SecurityConfigurationError,
    ProviderTimeoutError,
    ProviderRateLimitError,
    ProviderAPIError,
    MalformedModelResponseError,
    EvidenceGroundingError,
)
from backend.ai_gateway.provider import AIProviderAdapter
from backend.ai_gateway.gemini import GeminiInteractionsAdapter
from backend.ai_gateway.consent import ConsentManager, compute_packet_hash
from backend.ai_gateway.credentials import CredentialStore
from backend.ai_gateway.validator import EvidenceValidator
from backend.ai_gateway.gateway import AIGateway

__all__ = [
    "ClaimType",
    "StructuredClaim",
    "ExplanationResponse",
    "ConsentToken",
    "TransmissionPreview",
    "RawInteractionResponse",
    "ValidatedGatewayResult",
    "AIGatewayError",
    "ConsentViolationError",
    "CredentialMissingError",
    "SecurityConfigurationError",
    "ProviderTimeoutError",
    "ProviderRateLimitError",
    "ProviderAPIError",
    "MalformedModelResponseError",
    "EvidenceGroundingError",
    "AIProviderAdapter",
    "GeminiInteractionsAdapter",
    "ConsentManager",
    "compute_packet_hash",
    "CredentialStore",
    "EvidenceValidator",
    "AIGateway",
]
