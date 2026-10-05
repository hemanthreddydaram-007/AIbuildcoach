"""Conversation ingestion service enforcing user consent and secret redaction."""

from typing import Union, Dict, Any, Optional

from backend.domain.models import (
    Conversation,
    ConversationMessage,
    ConversationConsent,
    ConversationSource,
)
from backend.context_engine.secrets import detect_and_redact
from backend.conversation.adapters.factory import get_adapter
from backend.project_model.db import Database


class ConsentRequiredError(Exception):
    """Raised when conversation ingestion is attempted without explicit user consent."""
    pass


class ConversationIngestionService:
    """Orchestrates secure, consent-gated conversation normalization and persistence."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db

    def ingest(
        self,
        provider: str,
        raw_payload: Union[str, Dict[str, Any], list],
        consent: ConversationConsent,
        source: str = ConversationSource.IMPORT,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Conversation:
        """Ingests a conversation after verifying user consent and performing secret redaction."""
        # 1. Enforce user consent boundary before any data processing
        if not consent or not consent.approved:
            raise ConsentRequiredError(
                "Conversation ingestion rejected: explicit user approval is mandatory."
            )

        # 2. Record consent audit record if database is configured
        if self.db is not None:
            self.db.record_conversation_consent(consent)

        # 3. Retrieve adapter and normalize untrusted payload into domain model
        adapter = get_adapter(provider)
        conversation = adapter.normalize(
            raw_input=raw_payload,
            source=source,
            project_id=project_id,
            title=title,
        )

        # 4. Detect and redact sensitive values using existing M4 secrets engine
        total_redacted = 0
        sanitized_messages = []
        for msg in conversation.messages:
            redacted_text, was_redacted, summary = detect_and_redact(msg.content)
            meta = dict(msg.metadata)
            if was_redacted:
                total_redacted += summary.get("total_secrets_detected", 0)
                meta["redacted"] = True
                meta["redaction_summary"] = summary
            sanitized_messages.append(
                ConversationMessage(
                    message_id=msg.message_id,
                    role=msg.role,
                    content=redacted_text,
                    timestamp=msg.timestamp,
                    sequence=msg.sequence,
                    metadata=meta,
                )
            )

        conversation.messages = sanitized_messages
        if total_redacted > 0:
            conversation.metadata["total_secrets_redacted"] = total_redacted

        # 5. Persist normalized and redacted conversation atomically
        if self.db is not None:
            self.db.save_conversation(conversation)

        return conversation

    def normalize_only(
        self,
        provider: str,
        raw_payload: Union[str, Dict[str, Any], list],
        consent: ConversationConsent,
        source: str = ConversationSource.IMPORT,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Conversation:
        """Normalizes and redacts conversation payload without persisting to SQLite."""
        if not consent or not consent.approved:
            raise ConsentRequiredError(
                "Conversation normalization rejected: explicit user approval is mandatory."
            )

        adapter = get_adapter(provider)
        conversation = adapter.normalize(
            raw_input=raw_payload,
            source=source,
            project_id=project_id,
            title=title,
        )

        total_redacted = 0
        sanitized_messages = []
        for msg in conversation.messages:
            redacted_text, was_redacted, summary = detect_and_redact(msg.content)
            meta = dict(msg.metadata)
            if was_redacted:
                total_redacted += summary.get("total_secrets_detected", 0)
                meta["redacted"] = True
                meta["redaction_summary"] = summary
            sanitized_messages.append(
                ConversationMessage(
                    message_id=msg.message_id,
                    role=msg.role,
                    content=redacted_text,
                    timestamp=msg.timestamp,
                    sequence=msg.sequence,
                    metadata=meta,
                )
            )

        conversation.messages = sanitized_messages
        if total_redacted > 0:
            conversation.metadata["total_secrets_redacted"] = total_redacted

        return conversation
