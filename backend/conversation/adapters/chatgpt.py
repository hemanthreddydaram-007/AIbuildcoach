"""ChatGPT conversation adapter supporting OpenAI exports, message lists, and plain text."""

import re
from typing import Union, Dict, Any, List, Optional

from backend.domain.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationProvider,
    ConversationSource,
    utc_now_iso,
)
from backend.conversation.adapters.base import (
    ConversationAdapter,
    format_timestamp,
    generate_deterministic_id,
)


class ChatGPTAdapter(ConversationAdapter):
    """Normalizes ChatGPT/OpenAI conversation exports and payloads."""

    @property
    def provider(self) -> str:
        return ConversationProvider.CHATGPT

    def normalize(
        self,
        raw_input: Union[str, Dict[str, Any], list],
        source: str = ConversationSource.IMPORT,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Conversation:
        parsed = self._parse_raw(raw_input)

        if isinstance(parsed, dict) and "mapping" in parsed:
            return self._normalize_export_mapping(parsed, source, project_id, title)
        elif isinstance(parsed, dict) and "messages" in parsed and isinstance(parsed["messages"], list):
            return self._normalize_message_list(parsed, parsed["messages"], source, project_id, title)
        elif isinstance(parsed, list):
            return self._normalize_message_list({}, parsed, source, project_id, title)
        elif isinstance(parsed, str):
            return self._normalize_plain_text(parsed, source, project_id, title)
        else:
            # Fallback for empty or unknown dictionary structure
            conv_id = generate_deterministic_id("conv_chatgpt", str(parsed))
            return Conversation(
                conversation_id=conv_id,
                provider=self.provider,
                source=source,
                project_id=project_id,
                title=title,
                messages=[],
                metadata={"raw_shape": str(type(parsed))},
            )

    def _normalize_export_mapping(
        self,
        data: Dict[str, Any],
        source: str,
        project_id: Optional[str],
        title: Optional[str],
    ) -> Conversation:
        conv_id = str(data.get("id") or generate_deterministic_id("conv_chatgpt", json_str := str(data.get("title", ""))))
        conv_title = title or data.get("title")
        created_at = format_timestamp(data.get("create_time")) or utc_now_iso()
        updated_at = format_timestamp(data.get("update_time")) or created_at

        mapping = data.get("mapping", {})
        raw_messages = []

        # Find linear conversation tree
        for node_id, node in mapping.items():
            if not isinstance(node, dict):
                continue
            msg = node.get("message")
            if not msg or not isinstance(msg, dict):
                continue
            author = msg.get("author", {})
            role = author.get("role") if isinstance(author, dict) else None
            # Ignore internal router/tool/memory messages unless text content is present
            if role in ("system", "user", "assistant"):
                create_time = msg.get("create_time") or 0.0
                raw_messages.append((create_time, node_id, msg))

        # Sort chronologically by create_time
        raw_messages.sort(key=lambda item: item[0])

        normalized_messages: List[ConversationMessage] = []
        seq = 1
        for _, node_id, msg in raw_messages:
            msg_id = str(msg.get("id") or f"msg_{node_id}")
            role_raw = (msg.get("author", {}).get("role") or "assistant").lower()
            role = self._map_role(role_raw)
            content = self._extract_content(msg.get("content"))
            msg_ts = format_timestamp(msg.get("create_time"))

            # Retain non-sensitive model metadata
            metadata = {}
            if "model_slug" in msg.get("metadata", {}):
                metadata["model"] = msg["metadata"]["model_slug"]

            normalized_messages.append(
                ConversationMessage(
                    message_id=msg_id,
                    role=role,
                    content=content,
                    timestamp=msg_ts,
                    sequence=seq,
                    metadata=metadata,
                )
            )
            seq += 1

        return Conversation(
            conversation_id=conv_id,
            provider=self.provider,
            source=source,
            project_id=project_id,
            title=conv_title,
            created_at=created_at,
            updated_at=updated_at,
            messages=normalized_messages,
            metadata={"total_mapping_nodes": len(mapping)},
        )

    def _normalize_message_list(
        self,
        parent_dict: Dict[str, Any],
        messages: List[Any],
        source: str,
        project_id: Optional[str],
        title: Optional[str],
    ) -> Conversation:
        conv_id = str(parent_dict.get("id") or parent_dict.get("conversation_id") or generate_deterministic_id("conv_chatgpt", str(messages)))
        conv_title = title or parent_dict.get("title")
        created_at = format_timestamp(parent_dict.get("created_at") or parent_dict.get("create_time")) or utc_now_iso()
        updated_at = format_timestamp(parent_dict.get("updated_at") or parent_dict.get("update_time")) or created_at

        normalized_messages: List[ConversationMessage] = []
        seq = 1
        for idx, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            msg_id = str(m.get("id") or m.get("message_id") or f"{conv_id}_msg_{seq}")
            role_raw = str(m.get("role") or m.get("author") or "user").lower()
            role = self._map_role(role_raw)
            content = self._extract_content(m.get("content") or m.get("text") or m.get("parts") or "")
            msg_ts = format_timestamp(m.get("timestamp") or m.get("create_time") or m.get("created_at"))

            metadata = {}
            if "model" in m:
                metadata["model"] = m["model"]

            normalized_messages.append(
                ConversationMessage(
                    message_id=msg_id,
                    role=role,
                    content=content,
                    timestamp=msg_ts,
                    sequence=seq,
                    metadata=metadata,
                )
            )
            seq += 1

        return Conversation(
            conversation_id=conv_id,
            provider=self.provider,
            source=source,
            project_id=project_id,
            title=conv_title,
            created_at=created_at,
            updated_at=updated_at,
            messages=normalized_messages,
            metadata={},
        )

    def _normalize_plain_text(
        self,
        text: str,
        source: str,
        project_id: Optional[str],
        title: Optional[str],
    ) -> Conversation:
        conv_id = generate_deterministic_id("conv_chatgpt_txt", text)
        lines = text.strip().splitlines()

        normalized_messages: List[ConversationMessage] = []
        current_role: Optional[str] = None
        current_content: List[str] = []
        seq = 1

        pattern = re.compile(r"^(User|ChatGPT|Assistant|System):\s*(.*)$", re.IGNORECASE)

        for line in lines:
            m = pattern.match(line)
            if m:
                if current_role and current_content:
                    normalized_messages.append(
                        ConversationMessage(
                            message_id=f"{conv_id}_msg_{seq}",
                            role=self._map_role(current_role),
                            content="\n".join(current_content).strip(),
                            timestamp=None,
                            sequence=seq,
                            metadata={},
                        )
                    )
                    seq += 1
                    current_content = []
                current_role = m.group(1).lower()
                first_line = m.group(2)
                if first_line:
                    current_content.append(first_line)
            else:
                if current_role:
                    current_content.append(line)

        if current_role and current_content:
            normalized_messages.append(
                ConversationMessage(
                    message_id=f"{conv_id}_msg_{seq}",
                    role=self._map_role(current_role),
                    content="\n".join(current_content).strip(),
                    timestamp=None,
                    sequence=seq,
                    metadata={},
                )
            )

        now = utc_now_iso()
        return Conversation(
            conversation_id=conv_id,
            provider=self.provider,
            source=ConversationSource.PASTE if source == ConversationSource.IMPORT else source,
            project_id=project_id,
            title=title or "Pasted ChatGPT Conversation",
            created_at=now,
            updated_at=now,
            messages=normalized_messages,
            metadata={"format": "plain_text"},
        )

    def _map_role(self, role_str: str) -> str:
        r = role_str.strip().lower()
        if r in ("user", "human"):
            return ConversationRole.USER
        elif r in ("assistant", "chatgpt", "model"):
            return ConversationRole.ASSISTANT
        elif r == "system":
            return ConversationRole.SYSTEM
        return ConversationRole.USER

    def _extract_content(self, raw_content: Any) -> str:
        if isinstance(raw_content, str):
            return raw_content.strip()
        if isinstance(raw_content, dict):
            # Check OpenAI "parts" list: {"content_type": "text", "parts": ["..."]}
            parts = raw_content.get("parts")
            if isinstance(parts, list):
                return "\n".join(str(p) for p in parts if p is not None).strip()
            text = raw_content.get("text")
            if text:
                return str(text).strip()
        if isinstance(raw_content, list):
            return "\n".join(str(p) for p in raw_content if p is not None).strip()
        return str(raw_content).strip() if raw_content is not None else ""
