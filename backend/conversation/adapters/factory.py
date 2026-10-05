"""Factory for creating conversation adapters."""

from typing import Dict, Type
from backend.domain.models import ConversationProvider
from backend.conversation.adapters.base import ConversationAdapter
from backend.conversation.adapters.chatgpt import ChatGPTAdapter
from backend.conversation.adapters.claude import ClaudeAdapter
from backend.conversation.adapters.gemini import GeminiAdapter


ADAPTER_MAP: Dict[str, Type[ConversationAdapter]] = {
    ConversationProvider.CHATGPT.lower(): ChatGPTAdapter,
    ConversationProvider.CLAUDE.lower(): ClaudeAdapter,
    ConversationProvider.GEMINI.lower(): GeminiAdapter,
    "openai": ChatGPTAdapter,
    "anthropic": ClaudeAdapter,
    "google": GeminiAdapter,
}


def get_adapter(provider_name: str) -> ConversationAdapter:
    """Returns the registered ConversationAdapter for the given provider name."""
    clean = provider_name.strip().lower()
    adapter_cls = ADAPTER_MAP.get(clean)
    if not adapter_cls:
        raise ValueError(
            f"Unsupported conversation provider '{provider_name}'. Supported providers: "
            f"{', '.join(sorted(list(set(ADAPTER_MAP.keys()))))}"
        )
    return adapter_cls()
