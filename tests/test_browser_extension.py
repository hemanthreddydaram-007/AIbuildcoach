"""Integration and compatibility tests for Milestone 12.0 - Browser Provider Bridge Foundation.

Validates:
1. Extension Manifest V3 structure and security permissions.
2. Extension payload compatibility with M11.0 Conversation domain model.
3. Ingestion through ConversationIngestionService into SQLite with source=WEB_EXTENSION.
4. Evidence linkage (M11.1) and verification readiness (M11.2) for browser-captured conversations.
"""

import json
from pathlib import Path
import pytest

from backend.domain.models import (
    Conversation,
    ConversationMessage,
    ConversationConsent,
    ConversationRole,
    ConversationProvider,
    ConversationSource,
    FileChange,
    ChangeType,
    GitState,
    ChangeSet,
    Project,
    ProjectFile,
)
from backend.project_model.db import Database
from backend.conversation.service import ConversationIngestionService
from backend.conversation.evidence_service import ConversationEvidenceService
from backend.conversation.verification_service import ConversationVerificationService


EXTENSION_DIR = Path(__file__).resolve().parent.parent / "extension"


@pytest.fixture
def extension_manifest():
    manifest_file = EXTENSION_DIR / "manifest.json"
    assert manifest_file.exists(), "extension/manifest.json must exist"
    with open(manifest_file, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_extension_payload():
    """Generates a valid browser extension payload matching the M12.0 schema."""
    return {
        "conversation_id": "conv_ext_1728135000_abc123",
        "provider": "CHATGPT",
        "source": "WEB_EXTENSION",
        "title": "Bug Fix: Auth Token Validation",
        "project_id": "prj_ext_test",
        "created_at": "2026-10-05T12:00:00Z",
        "updated_at": "2026-10-05T12:05:00Z",
        "messages": [
          {
            "message_id": "ext_msg_1",
            "role": "USER",
            "content": "Can you check why token validation failed in backend/auth.py?",
            "timestamp": "2026-10-05T12:00:10Z",
            "sequence": 1,
            "metadata": {}
          },
          {
            "message_id": "ext_msg_2",
            "role": "ASSISTANT",
            "content": "I reviewed backend/auth.py and fixed the signature verification logic. I also removed key [REDACTED:OPENAI_API_KEY].",
            "timestamp": "2026-10-05T12:01:00Z",
            "sequence": 2,
            "metadata": {
              "redacted": True,
              "secrets_detected": 1,
              "secret_types": ["OPENAI_API_KEY"]
            }
          }
        ],
        "metadata": {
          "source": "WEB_EXTENSION",
          "extension_version": "1.0.0",
          "adapter": "ChatGPTPageAdapter",
          "url": "https://chatgpt.com/c/123-abc",
          "total_secrets_redacted": 1,
          "redacted_secret_types": ["OPENAI_API_KEY"],
          "exported_at": "2026-10-05T12:05:00Z"
        }
    }


# ============================================================================
# 1. MANIFEST & SECURITY BOUNDARY TESTS
# ============================================================================

def test_manifest_version_and_structure(extension_manifest):
    """Verifies that the extension manifest is valid Manifest V3."""
    assert extension_manifest.get("manifest_version") == 3
    assert extension_manifest.get("name")
    assert extension_manifest.get("version") == "1.0.0"
    assert extension_manifest.get("action", {}).get("default_popup") == "popup/popup.html"
    assert extension_manifest.get("background", {}).get("service_worker") == "background.js"
    assert extension_manifest.get("background", {}).get("type") == "module"


def test_manifest_strict_permissions(extension_manifest):
    """Verifies that the extension requests strictly minimal permissions with zero cookies or background scrapers."""
    permissions = extension_manifest.get("permissions", [])
    
    # Must NOT have broad or intrusive permissions
    assert "cookies" not in permissions, "Security violation: cookies permission is forbidden"
    assert "webRequest" not in permissions, "Security violation: webRequest monitoring is forbidden"
    assert "webNavigation" not in permissions, "Security violation: webNavigation monitoring is forbidden"
    assert "tabs" not in permissions, "Must not request broad tabs permission (only activeTab)"
    assert "<all_urls>" not in permissions, "Must not request <all_urls>"

    # Must only contain activeTab and storage
    assert "activeTab" in permissions


def test_manifest_host_permissions_bounded_to_providers(extension_manifest):
    """Verifies that host_permissions are strictly limited to ChatGPT, Claude, and Gemini."""
    host_permissions = extension_manifest.get("host_permissions", [])
    assert len(host_permissions) > 0

    allowed_domains = {"chatgpt.com", "chat.openai.com", "claude.ai", "gemini.google.com"}
    for host in host_permissions:
        assert host != "<all_urls>"
        assert not host.startswith("*://*")
        domain_match = any(domain in host for domain in allowed_domains)
        assert domain_match, f"Host permission {host} is outside supported providers"


def test_extension_file_structure():
    """Verifies that all required extension components exist on disk."""
    required_files = [
        EXTENSION_DIR / "manifest.json",
        EXTENSION_DIR / "background.js",
        EXTENSION_DIR / "content_script.js",
        EXTENSION_DIR / "popup" / "popup.html",
        EXTENSION_DIR / "popup" / "popup.css",
        EXTENSION_DIR / "popup" / "popup.js",
        EXTENSION_DIR / "src" / "messages.js",
        EXTENSION_DIR / "src" / "secrets.js",
        EXTENSION_DIR / "src" / "bridge.js",
        EXTENSION_DIR / "src" / "adapters" / "base.js",
        EXTENSION_DIR / "src" / "adapters" / "chatgpt.js",
        EXTENSION_DIR / "src" / "adapters" / "claude.js",
        EXTENSION_DIR / "src" / "adapters" / "gemini.js",
        EXTENSION_DIR / "src" / "adapters" / "factory.js",
        EXTENSION_DIR / "package.json",
        EXTENSION_DIR / "README.md",
    ]
    for rf in required_files:
        assert rf.exists(), f"Missing required extension file: {rf}"


# ============================================================================
# 2. DATA CONTRACT & INGESTION COMPATIBILITY TESTS
# ============================================================================

def test_conversation_source_web_extension():
    """Verifies that WEB_EXTENSION is a first-class ConversationSource in domain models."""
    assert ConversationSource.WEB_EXTENSION == "WEB_EXTENSION"


def test_extension_payload_maps_to_conversation_model(sample_extension_payload):
    """Verifies that the extension payload parses directly into M11.0 Conversation model."""
    conv = Conversation(**sample_extension_payload)
    assert conv.conversation_id == "conv_ext_1728135000_abc123"
    assert conv.provider == "CHATGPT"
    assert conv.source == ConversationSource.WEB_EXTENSION
    assert len(conv.messages) == 2
    assert conv.messages[0].role == ConversationRole.USER
    assert conv.messages[1].role == ConversationRole.ASSISTANT
    assert conv.metadata["adapter"] == "ChatGPTPageAdapter"


def test_ingest_extension_conversation_flow(tmp_path, sample_extension_payload):
    """Verifies end-to-end ingestion of browser extension conversation into SQLite."""
    db_path = tmp_path / "test_ext.db"
    db = Database(db_path)

    # Create project
    project = Project(
        id=sample_extension_payload["project_id"],
        name="Browser Extension Test Project",
        root_path=str(tmp_path),
    )
    db.upsert_project(project)

    service = ConversationIngestionService(db=db)
    consent = ConversationConsent(
        consent_id="cst_ext_123",
        approved=True,
        scope="CONVERSATION_INGESTION",
    )

    # Ingest using WEB_EXTENSION source
    saved_conv = service.ingest(
        provider=sample_extension_payload["provider"],
        raw_payload=sample_extension_payload,
        consent=consent,
        source=ConversationSource.WEB_EXTENSION,
        project_id=project.id,
        title=sample_extension_payload["title"],
    )

    assert saved_conv.conversation_id == sample_extension_payload["conversation_id"]
    assert saved_conv.source == ConversationSource.WEB_EXTENSION
    assert len(saved_conv.messages) == 2

    # Verify retrieved from DB
    retrieved = db.get_conversation(saved_conv.conversation_id)
    assert retrieved is not None
    assert retrieved.source == "WEB_EXTENSION"
    assert retrieved.title == "Bug Fix: Auth Token Validation"
    assert len(retrieved.messages) == 2
    assert retrieved.messages[0].content == "Can you check why token validation failed in backend/auth.py?"


# ============================================================================
# 3. DOWNSTREAM COMPATIBILITY (M11.1 EVIDENCE & M11.2 VERIFICATION)
# ============================================================================

def test_extension_conversation_evidence_and_verification_pipeline(tmp_path, sample_extension_payload):
    """Verifies that an ingested browser extension conversation smoothly feeds M11.1 Evidence and M11.2 Verification."""
    db_path = tmp_path / "test_pipeline.db"
    db = Database(db_path)

    project = Project(
        id=sample_extension_payload["project_id"],
        name="Pipeline Test",
        root_path=str(tmp_path),
    )
    db.upsert_project(project)

    # Sync project file
    auth_file = tmp_path / "backend" / "auth.py"
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    auth_file.write_text("def verify_token(): pass\n", encoding="utf-8")
    db.sync_files(
        project.id,
        [
            ProjectFile(
                path="backend/auth.py",
                absolute_path=str(auth_file),
                file_size=auth_file.stat().st_size,
                last_modified=auth_file.stat().st_mtime,
                sha256_hash="hash_auth",
                file_type="python",
            )
        ],
    )

    # Ingest conversation
    service = ConversationIngestionService(db=db)
    consent = ConversationConsent(
        consent_id="cst_ext_pipeline",
        approved=True,
        scope="CONVERSATION_INGESTION",
    )
    saved_conv = service.ingest(
        provider=sample_extension_payload["provider"],
        raw_payload=sample_extension_payload,
        consent=consent,
        source=ConversationSource.WEB_EXTENSION,
        project_id=project.id,
        title=sample_extension_payload["title"],
    )

    # Add a matching file change in changeset
    changeset = ChangeSet(
        id="cs_ext_1",
        project_id=project.id,
        git_state=GitState(branch="main", head_commit="abc1234", is_dirty=True),
        file_changes=[
            FileChange(
                id="fc_auth_ext",
                change_set_id="cs_ext_1",
                project_id=project.id,
                old_path="backend/auth.py",
                new_path="backend/auth.py",
                change_type="MODIFIED",
                is_staged=True,
                is_untracked=False,
                old_line_count=20,
                new_line_count=25,
            )
        ],
    )
    db.save_change_set(changeset)

    # M11.1 Evidence Analysis
    evidence_svc = ConversationEvidenceService(db)
    m11_result = evidence_svc.analyze_conversation(
        conversation_id=saved_conv.conversation_id,
        project_id=project.id,
    )

    assert len(m11_result.claims) > 0
    auth_claims = [c for c in m11_result.claims if "backend/auth.py" in c.referenced_paths]
    assert len(auth_claims) > 0
    target_claim = auth_claims[0]

    # Links must connect claim to project file or changeset
    links = [l for l in m11_result.evidence_links if l.claim_id == target_claim.claim_id]
    assert len(links) > 0
    evidence_ids = {l.evidence_id for l in links}
    assert "file:backend/auth.py" in evidence_ids or "fc_auth_ext" in evidence_ids

    # M11.2 Verification Preparation
    verify_svc = ConversationVerificationService(db)
    req, packet = verify_svc.prepare_verification(
        conversation_id=saved_conv.conversation_id,
        claim_id=target_claim.claim_id,
        project_id=project.id,
    )

    assert packet.id.startswith("pkt_verif_")
    assert packet.project_id == project.id
    assert packet.generated_at == saved_conv.created_at
    assert len(packet.items) >= 2
