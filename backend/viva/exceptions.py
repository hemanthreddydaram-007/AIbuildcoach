"""Custom domain exceptions for Milestone 8: Viva Defence Engine."""


class VivaError(Exception):
    """Base exception for Viva Defence workflow."""
    pass


class VivaSessionNotFoundError(VivaError):
    """Raised when a requested viva session is not found in database."""
    pass


class TurnLimitExceededError(VivaError):
    """Raised when max turn limits (base questions, followups, or total turns) are exceeded."""
    pass


class InvalidTurnProgressionError(VivaError):
    """Raised when a turn sequence is invalid, out of order, or already answered."""
    pass


class ConcurrentSessionError(VivaError):
    """Raised when an operation collides with a session currently EVALUATING."""
    pass


class InsufficientEvidenceError(VivaError):
    """Raised when repository evidence is insufficient to formulate grounded questions."""
    pass
