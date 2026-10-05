"""Provider-neutral conversation adapters."""

from backend.conversation.adapters.base import ConversationAdapter
from backend.conversation.adapters.chatgpt import ChatGPTAdapter
from backend.conversation.adapters.claude import ClaudeAdapter
from backend.conversation.adapters.gemini import GeminiAdapter
from backend.conversation.adapters.factory import get_adapter

__all__ = [
    "ConversationAdapter",
    "ChatGPTAdapter",
    "ClaudeAdapter",
    "GeminiAdapter",
    "get_adapter",
]
