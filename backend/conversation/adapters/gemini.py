"""Gemini conversation adapter supporting Google AI Studio / Gemini JSON, message lists, and plain text."""

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


class GeminiAdapter(ConversationAdapter):
    """Normalizes Gemini/Google conversation payloads and exports."""

    @property
    def provider(self) -> str:
        return ConversationProvider.GEMINI

    def normalize(
        self,
        raw_input: Union[str, Dict[str, Any], list],
        source: str = ConversationSource.IMPORT,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Conversation:
        parsed = self._parse_raw(raw_input)

        if isinstance(parsed, dict) and "contents" in parsed and isinstance(parsed["contents"], list):
            return self._normalize_contents(parsed, parsed["contents"], source, project_id, title)
        elif isinstance(parsed, dict) and "messages" in parsed and isinstance(parsed["messages"], list):
            return self._normalize_messages(parsed, parsed["messages"], source, project_id, title)
        elif isinstance(parsed, list):
            # Check if list of contents with "parts" or list of standard messages
            if parsed and isinstance(parsed[0], dict) and "parts" in parsed[0]:
                return self._normalize_contents({}, parsed, source, project_id, title)
            return self._normalize_messages({}, parsed, source, project_id, title)
        elif isinstance(parsed, str):
            return self._normalize_plain_text(parsed, source, project_id, title)
        else:
            conv_id = generate_deterministic_id("conv_gemini", str(parsed))
            return Conversation(
                conversation_id=conv_id,
                provider=self.provider,
                source=source,
                project_id=project_id,
                title=title,
                messages=[],
                metadata={"raw_shape": str(type(parsed))},
            )

    def _normalize_contents(
        self,
        parent_dict: Dict[str, Any],
        contents: List[Any],
        source: str,
        project_id: Optional[str],
        title: Optional[str],
    ) -> Conversation:
        conv_id = str(parent_dict.get("id") or parent_dict.get("name") or generate_deterministic_id("conv_gemini", str(contents)))
        conv_title = title or parent_dict.get("title") or parent_dict.get("name")
        created_at = format_timestamp(parent_dict.get("created_at") or parent_dict.get("create_time")) or utc_now_iso()
        updated_at = format_timestamp(parent_dict.get("updated_at") or parent_dict.get("update_time")) or created_at

        normalized_messages: List[ConversationMessage] = []
        seq = 1

        for c in contents:
            if not isinstance(c, dict):
                continue
            msg_id = str(c.get("id") or f"{conv_id}_msg_{seq}")
            role_raw = str(c.get("role") or "user")
            role = self._map_role(role_raw)
            content = self._extract_parts_content(c.get("parts"))
            msg_ts = format_timestamp(c.get("timestamp") or c.get("created_at"))

            metadata = {}
            if "model" in c:
                metadata["model"] = c["model"]

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
            metadata={"total_contents": len(contents)},
        )

    def _normalize_messages(
        self,
        parent_dict: Dict[str, Any],
        messages: List[Any],
        source: str,
        project_id: Optional[str],
        title: Optional[str],
    ) -> Conversation:
        conv_id = str(parent_dict.get("id") or generate_deterministic_id("conv_gemini", str(messages)))
        conv_title = title or parent_dict.get("title")
        created_at = format_timestamp(parent_dict.get("created_at")) or utc_now_iso()
        updated_at = format_timestamp(parent_dict.get("updated_at")) or created_at

        normalized_messages: List[ConversationMessage] = []
        seq = 1

        for m in messages:
            if not isinstance(m, dict):
                continue
            msg_id = str(m.get("id") or f"{conv_id}_msg_{seq}")
            author = str(m.get("author") or m.get("role") or "user")
            role = self._map_role(author)
            content = self._extract_parts_content(m.get("content") or m.get("text") or m.get("parts") or "")
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
        conv_id = generate_deterministic_id("conv_gemini_txt", text)
        lines = text.strip().splitlines()

        normalized_messages: List[ConversationMessage] = []
        current_role: Optional[str] = None
        current_content: List[str] = []
        seq = 1

        pattern = re.compile(r"^(User|Gemini|Model|Assistant|System):\s*(.*)$", re.IGNORECASE)

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
            title=title or "Pasted Gemini Conversation",
            created_at=now,
            updated_at=now,
            messages=normalized_messages,
            metadata={"format": "plain_text"},
        )

    def _map_role(self, role_str: str) -> str:
        r = role_str.strip().lower()
        if r in ("user", "human"):
            return ConversationRole.USER
        elif r in ("model", "gemini", "assistant"):
            return ConversationRole.ASSISTANT
        elif r == "system":
            return ConversationRole.SYSTEM
        return ConversationRole.USER

    def _extract_parts_content(self, parts_data: Any) -> str:
        if isinstance(parts_data, str):
            return parts_data.strip()
        if isinstance(parts_data, list):
            texts = []
            for p in parts_data:
                if isinstance(p, dict) and "text" in p:
                    texts.append(str(p["text"]))
                elif isinstance(p, str):
                    texts.append(p)
            return "\n".join(t for t in texts if t).strip()
        if isinstance(parts_data, dict):
            if "text" in parts_data:
                return str(parts_data["text"]).strip()
        return str(parts_data).strip() if parts_data is not None else ""
