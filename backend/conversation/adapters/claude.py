"""Claude conversation adapter supporting Anthropic exports, message lists, and plain text."""

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


class ClaudeAdapter(ConversationAdapter):
    """Normalizes Claude/Anthropic conversation exports and payloads."""

    @property
    def provider(self) -> str:
        return ConversationProvider.CLAUDE

    def normalize(
        self,
        raw_input: Union[str, Dict[str, Any], list],
        source: str = ConversationSource.IMPORT,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Conversation:
        parsed = self._parse_raw(raw_input)

        if isinstance(parsed, dict) and "chat_messages" in parsed and isinstance(parsed["chat_messages"], list):
            return self._normalize_export(parsed, source, project_id, title)
        elif isinstance(parsed, dict) and "messages" in parsed and isinstance(parsed["messages"], list):
            return self._normalize_messages(parsed, parsed["messages"], source, project_id, title)
        elif isinstance(parsed, list):
            return self._normalize_messages({}, parsed, source, project_id, title)
        elif isinstance(parsed, str):
            return self._normalize_plain_text(parsed, source, project_id, title)
        else:
            conv_id = generate_deterministic_id("conv_claude", str(parsed))
            return Conversation(
                conversation_id=conv_id,
                provider=self.provider,
                source=source,
                project_id=project_id,
                title=title,
                messages=[],
                metadata={"raw_shape": str(type(parsed))},
            )

    def _normalize_export(
        self,
        data: Dict[str, Any],
        source: str,
        project_id: Optional[str],
        title: Optional[str],
    ) -> Conversation:
        conv_id = str(data.get("uuid") or data.get("id") or generate_deterministic_id("conv_claude", str(data.get("name", ""))))
        conv_title = title or data.get("name")
        created_at = format_timestamp(data.get("created_at")) or utc_now_iso()
        updated_at = format_timestamp(data.get("updated_at")) or created_at

        raw_messages = data.get("chat_messages", [])
        normalized_messages: List[ConversationMessage] = []
        seq = 1

        for m in raw_messages:
            if not isinstance(m, dict):
                continue
            msg_id = str(m.get("uuid") or m.get("id") or f"{conv_id}_msg_{seq}")
            sender = str(m.get("sender") or m.get("role") or "human")
            role = self._map_role(sender)
            content = self._extract_content(m.get("text") or m.get("content") or "")
            msg_ts = format_timestamp(m.get("created_at") or m.get("timestamp"))

            metadata = {}
            if "model" in m:
                metadata["model"] = m["model"]
            if "attachments" in m and m["attachments"]:
                metadata["attachments_count"] = len(m["attachments"])

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
            metadata={"total_chat_messages": len(raw_messages)},
        )

    def _normalize_messages(
        self,
        parent_dict: Dict[str, Any],
        messages: List[Any],
        source: str,
        project_id: Optional[str],
        title: Optional[str],
    ) -> Conversation:
        conv_id = str(parent_dict.get("id") or parent_dict.get("uuid") or generate_deterministic_id("conv_claude", str(messages)))
        conv_title = title or parent_dict.get("name") or parent_dict.get("title")
        created_at = format_timestamp(parent_dict.get("created_at")) or utc_now_iso()
        updated_at = format_timestamp(parent_dict.get("updated_at")) or created_at

        normalized_messages: List[ConversationMessage] = []
        seq = 1

        for m in messages:
            if not isinstance(m, dict):
                continue
            msg_id = str(m.get("id") or m.get("uuid") or f"{conv_id}_msg_{seq}")
            sender = str(m.get("role") or m.get("sender") or "user")
            role = self._map_role(sender)
            content = self._extract_content(m.get("content") or m.get("text") or "")
            msg_ts = format_timestamp(m.get("timestamp") or m.get("created_at"))

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
        conv_id = generate_deterministic_id("conv_claude_txt", text)
        lines = text.strip().splitlines()

        normalized_messages: List[ConversationMessage] = []
        current_role: Optional[str] = None
        current_content: List[str] = []
        seq = 1

        pattern = re.compile(r"^(Human|Claude|Assistant|System):\s*(.*)$", re.IGNORECASE)

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
            title=title or "Pasted Claude Conversation",
            created_at=now,
            updated_at=now,
            messages=normalized_messages,
            metadata={"format": "plain_text"},
        )

    def _map_role(self, sender: str) -> str:
        s = sender.strip().lower()
        if s in ("human", "user"):
            return ConversationRole.USER
        elif s in ("assistant", "claude", "model"):
            return ConversationRole.ASSISTANT
        elif s == "system":
            return ConversationRole.SYSTEM
        return ConversationRole.USER

    def _extract_content(self, raw_content: Any) -> str:
        if isinstance(raw_content, str):
            return raw_content.strip()
        if isinstance(raw_content, list):
            # Check Anthropic blocks: [{"type": "text", "text": "..."}]
            texts = []
            for b in raw_content:
                if isinstance(b, dict) and b.get("type") == "text":
                    texts.append(b.get("text", ""))
                elif isinstance(b, str):
                    texts.append(b)
                elif isinstance(b, dict) and "text" in b:
                    texts.append(str(b["text"]))
            return "\n".join(t for t in texts if t).strip()
        if isinstance(raw_content, dict):
            return str(raw_content.get("text") or raw_content.get("content") or "").strip()
        return str(raw_content).strip() if raw_content is not None else ""
