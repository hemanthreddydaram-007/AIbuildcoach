"""AI Gateway exception hierarchy."""


class AIGatewayError(Exception):
    """Base exception for all AI Gateway operations."""
    pass


class ConsentViolationError(AIGatewayError):
    """Raised when user consent is missing, expired, or bound parameters mismatch."""
    pass


class CredentialMissingError(AIGatewayError):
    """Raised when the required API key cannot be resolved."""
    pass


class SecurityConfigurationError(AIGatewayError):
    """Raised when sensitive credentials are inappropriately configured (e.g. in config.json)."""
    pass


class ProviderTimeoutError(AIGatewayError):
    """Raised when a provider request exceeds the configured network timeout."""
    pass


class ProviderRateLimitError(AIGatewayError):
    """Raised when a provider returns HTTP 429 Too Many Requests."""
    pass


class ProviderAPIError(AIGatewayError):
    """Raised when an external provider returns an HTTP error code."""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


class MalformedModelResponseError(AIGatewayError):
    """Raised when the model response cannot be parsed into the expected JSON schema."""
    pass


class EvidenceGroundingError(AIGatewayError):
    """Raised when model claims fail integrity checks or lack evidence grounding."""
    pass
