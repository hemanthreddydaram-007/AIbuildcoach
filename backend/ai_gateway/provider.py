"""Abstract provider adapter contract for AI Gateway."""

from abc import ABC, abstractmethod
from typing import Dict, Any

from backend.ai_gateway.models import RawInteractionResponse


class AIProviderAdapter(ABC):
    """Abstract interface that all AI provider transports must implement."""

    @abstractmethod
    def get_provider_name(self) -> str:
        """Returns the canonical provider name (e.g. 'gemini')."""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Returns the canonical model name (e.g. 'gemini-3.8-flash')."""
        pass

    @abstractmethod
    def complete_interaction(
        self,
        system_instruction: str,
        user_input: str,
        response_format: Dict[str, Any],
        api_key: str,
        timeout: float = 30.0,
    ) -> RawInteractionResponse:
        """Executes a structured interaction against the remote provider API.
        
        Args:
            system_instruction: Plain string system prompt
            user_input: Plain string prompt with fenced untrusted project evidence
            response_format: Schema specification for structured output
            api_key: Resolved API key for authentication
            timeout: Network request timeout in seconds
            
        Returns:
            RawInteractionResponse containing the HTTP status code, raw JSON, and latency
        """
        pass
