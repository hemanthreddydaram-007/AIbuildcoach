"""Custom exceptions for Milestone 7: Can I Explain This? Comprehension Loop."""


class ComprehensionError(Exception):
    """Base exception for comprehension workflow errors."""
    pass


class ContextBindingMismatchError(ComprehensionError):
    """Raised when a submission does not match active M6 UnderstandChangeResult context."""
    pass


class InvalidAttemptProgressionError(ComprehensionError):
    """Raised when an attempt sequence is out of order or invalid."""
    pass


class ConcurrentAttemptError(ComprehensionError):
    """Raised when an attempt is already in progress concurrently."""
    pass
