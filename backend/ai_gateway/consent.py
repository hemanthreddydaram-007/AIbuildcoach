"""Consent management and cryptographic binding for AI Gateway."""

import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.domain.models import ContextPacket, utc_now_iso
from backend.ai_gateway.models import ConsentToken, TransmissionPreview
from backend.ai_gateway.exceptions import ConsentViolationError


def compute_packet_hash(packet: ContextPacket) -> str:
    """Computes a deterministic SHA-256 digest over the canonical representation of a ContextPacket."""
    raw = json.dumps(packet.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ConsentManager:
    """Manages and validates cryptographically bound user consent for external context transmission."""

    @staticmethod
    def create_preview(
        packet: ContextPacket,
        provider: str = "gemini",
        model: str = "gemini-3.8-flash",
    ) -> TransmissionPreview:
        """Prepares a human-inspectable transmission preview before consent is requested."""
        files_included = sorted({item.file_path for item in packet.items if item.file_path})
        packet_hash = compute_packet_hash(packet)
        return TransmissionPreview(
            packet_id=packet.id,
            packet_hash=packet_hash,
            provider=provider,
            model=model,
            token_estimate=packet.token_estimate,
            files_included=files_included,
            redaction_summary=packet.redaction_summary,
            item_count=len(packet.items),
        )

    @staticmethod
    def grant_consent(
        packet: ContextPacket,
        provider: str = "gemini",
        model: str = "gemini-3.8-flash",
        duration_minutes: int = 15,
    ) -> ConsentToken:
        """Issues a cryptographically bound ConsentToken for the specific packet, provider, and model."""
        now = datetime.now(timezone.utc)
        approved_at = now.isoformat()
        expires_at = (now + timedelta(minutes=duration_minutes)).isoformat()
        packet_hash = compute_packet_hash(packet)
        
        token_sig = f"{packet.id}:{packet_hash}:{provider}:{model}:{approved_at}"
        token_id = f"cst_{hashlib.sha256(token_sig.encode('utf-8')).hexdigest()[:16]}"

        return ConsentToken(
            token_id=token_id,
            packet_id=packet.id,
            packet_hash=packet_hash,
            provider=provider,
            model=model,
            approved_at=approved_at,
            expires_at=expires_at,
            user_acknowledged=True,
        )

    @staticmethod
    def validate_consent(
        packet: ContextPacket,
        token: ConsentToken,
        provider: str = "gemini",
        model: str = "gemini-3.8-flash",
    ) -> None:
        """Validates that a ConsentToken is valid, non-expired, and strictly bound to the target packet."""
        if not token.user_acknowledged:
            raise ConsentViolationError("Transmission rejected: user has not explicitly acknowledged consent.")

        if token.packet_id != packet.id:
            raise ConsentViolationError(
                f"Consent packet ID mismatch: token authorized '{token.packet_id}', but target packet is '{packet.id}'."
            )

        current_hash = compute_packet_hash(packet)
        if token.packet_hash != current_hash:
            raise ConsentViolationError(
                "Consent packet hash mismatch: packet contents have been altered after consent was granted."
            )

        if token.provider != provider:
            raise ConsentViolationError(
                f"Consent provider mismatch: token authorized '{token.provider}', but request targeted '{provider}'."
            )

        if token.model != model:
            raise ConsentViolationError(
                f"Consent model mismatch: token authorized '{token.model}', but request targeted '{model}'."
            )

        now = datetime.now(timezone.utc)
        try:
            expires_dt = datetime.fromisoformat(token.expires_at)
            if now > expires_dt:
                raise ConsentViolationError(
                    f"Consent token expired at {token.expires_at} (current time: {now.isoformat()})."
                )
        except ValueError as exc:
            raise ConsentViolationError(f"Malformed consent expiration timestamp: {token.expires_at}") from exc
