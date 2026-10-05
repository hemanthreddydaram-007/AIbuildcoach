"""Comprehensive test suite for Milestone 11.0: Conversation Bridge Foundation."""

import io
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from backend.domain.models import (
    Conversation,
    ConversationMessage,
    ConversationRole,
    ConversationProvider,
    ConversationSource,
    ConversationConsent,
)
from backend.conversation.adapters.chatgpt import ChatGPTAdapter
from backend.conversation.adapters.claude import ClaudeAdapter
from backend.conversation.adapters.gemini import GeminiAdapter
from backend.conversation.adapters.factory import get_adapter
from backend.conversation.service import ConversationIngestionService, ConsentRequiredError
from backend.project_model.db import Database
from backend.cli.main import main


# ---------------------------------------------------------------------------
# FIXTURES: Semantically equivalent conversation across 3 providers
# ---------------------------------------------------------------------------

CHATGPT_MAPPING_FIXTURE = {
    "id": "chatgpt_conv_001",
    "title": "JWT Auth Explanation",
    "create_time": 1704067200.0,
    "update_time": 1704067300.0,
    "mapping": {
        "node_root": {
            "id": "node_root",
            "message": None,
        },
        "node_msg_1": {
            "id": "node_msg_1",
            "parent": "node_root",
            "message": {
                "id": "m_user_1",
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": ["How do I implement JWT auth safely?"]},
                "create_time": 1704067200.0,
                "metadata": {},
            },
        },
        "node_msg_2": {
            "id": "node_msg_2",
            "parent": "node_msg_1",
            "message": {
                "id": "m_asst_1",
                "author": {"role": "assistant"},
                "content": {"content_type": "text", "parts": ["Always store tokens securely and sign them with a strong secret."]},
                "create_time": 1704067210.0,
                "metadata": {"model_slug": "gpt-4o"},
            },
        },
    },
}

CLAUDE_EXPORT_FIXTURE = {
    "uuid": "claude_conv_001",
    "name": "JWT Auth Explanation",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:01:40Z",
    "chat_messages": [
        {
            "uuid": "c_msg_1",
            "sender": "human",
            "text": "How do I implement JWT auth safely?",
            "created_at": "2024-01-01T00:00:00Z",
        },
        {
            "uuid": "c_msg_2",
            "sender": "assistant",
            "text": "Always store tokens securely and sign them with a strong secret.",
            "created_at": "2024-01-01T00:00:10Z",
            "model": "claude-3-5-sonnet",
        },
    ],
}

GEMINI_CONTENTS_FIXTURE = {
    "id": "gemini_conv_001",
    "title": "JWT Auth Explanation",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:01:40Z",
    "contents": [
        {
            "id": "g_msg_1",
            "role": "user",
            "parts": [{"text": "How do I implement JWT auth safely?"}],
            "timestamp": "2024-01-01T00:00:00Z",
        },
        {
            "id": "g_msg_2",
            "role": "model",
            "parts": [{"text": "Always store tokens securely and sign them with a strong secret."}],
            "timestamp": "2024-01-01T00:00:10Z",
            "model": "gemini-1.5-pro",
        },
    ],
}


# ---------------------------------------------------------------------------
# 1. ADAPTER NORMALIZATION TESTS
# ---------------------------------------------------------------------------

def test_chatgpt_adapter_normalization_mapping():
    adapter = ChatGPTAdapter()
    conv = adapter.normalize(CHATGPT_MAPPING_FIXTURE)

    assert conv.provider == ConversationProvider.CHATGPT
    assert conv.conversation_id == "chatgpt_conv_001"
    assert conv.title == "JWT Auth Explanation"
    assert len(conv.messages) == 2

    assert conv.messages[0].role == ConversationRole.USER
    assert conv.messages[0].content == "How do I implement JWT auth safely?"
    assert conv.messages[0].sequence == 1
    assert conv.messages[0].timestamp is not None

    assert conv.messages[1].role == ConversationRole.ASSISTANT
    assert conv.messages[1].content == "Always store tokens securely and sign them with a strong secret."
    assert conv.messages[1].sequence == 2
    assert conv.messages[1].metadata.get("model") == "gpt-4o"


def test_chatgpt_adapter_normalization_message_list():
    adapter = ChatGPTAdapter()
    payload = {
        "id": "chatgpt_list_123",
        "title": "Simple Chat",
        "messages": [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
        ],
    }
    conv = adapter.normalize(payload)
    assert conv.conversation_id == "chatgpt_list_123"
    assert len(conv.messages) == 2
    assert conv.messages[0].role == ConversationRole.USER
    assert conv.messages[1].role == ConversationRole.ASSISTANT


def test_claude_adapter_normalization():
    adapter = ClaudeAdapter()
    conv = adapter.normalize(CLAUDE_EXPORT_FIXTURE)

    assert conv.provider == ConversationProvider.CLAUDE
    assert conv.conversation_id == "claude_conv_001"
    assert conv.title == "JWT Auth Explanation"
    assert len(conv.messages) == 2

    assert conv.messages[0].role == ConversationRole.USER
    assert conv.messages[0].content == "How do I implement JWT auth safely?"
    assert conv.messages[0].sequence == 1

    assert conv.messages[1].role == ConversationRole.ASSISTANT
    assert conv.messages[1].content == "Always store tokens securely and sign them with a strong secret."
    assert conv.messages[1].sequence == 2
    assert conv.messages[1].metadata.get("model") == "claude-3-5-sonnet"


def test_gemini_adapter_normalization():
    adapter = GeminiAdapter()
    conv = adapter.normalize(GEMINI_CONTENTS_FIXTURE)

    assert conv.provider == ConversationProvider.GEMINI
    assert conv.conversation_id == "gemini_conv_001"
    assert conv.title == "JWT Auth Explanation"
    assert len(conv.messages) == 2

    assert conv.messages[0].role == ConversationRole.USER
    assert conv.messages[0].content == "How do I implement JWT auth safely?"
    assert conv.messages[0].sequence == 1

    assert conv.messages[1].role == ConversationRole.ASSISTANT
    assert conv.messages[1].content == "Always store tokens securely and sign them with a strong secret."
    assert conv.messages[1].sequence == 2
    assert conv.messages[1].metadata.get("model") == "gemini-1.5-pro"


# ---------------------------------------------------------------------------
# 2. EQUIVALENT CONVERSATION NORMALIZATION
# ---------------------------------------------------------------------------

def test_equivalent_conversations_across_all_three_providers():
    """Validates that semantically identical conversations normalize into equivalent models."""
    chatgpt_conv = ChatGPTAdapter().normalize(CHATGPT_MAPPING_FIXTURE)
    claude_conv = ClaudeAdapter().normalize(CLAUDE_EXPORT_FIXTURE)
    gemini_conv = GeminiAdapter().normalize(GEMINI_CONTENTS_FIXTURE)

    assert len(chatgpt_conv.messages) == len(claude_conv.messages) == len(gemini_conv.messages) == 2

    for c in (chatgpt_conv, claude_conv, gemini_conv):
        # 1. First message: User question
        assert c.messages[0].role == ConversationRole.USER
        assert c.messages[0].content == "How do I implement JWT auth safely?"
        assert c.messages[0].sequence == 1

        # 2. Second message: Assistant answer
        assert c.messages[1].role == ConversationRole.ASSISTANT
        assert c.messages[1].content == "Always store tokens securely and sign them with a strong secret."
        assert c.messages[1].sequence == 2


# ---------------------------------------------------------------------------
# 3. PLAIN TEXT / PASTE SUPPORT
# ---------------------------------------------------------------------------

def test_plain_text_paste_normalization():
    paste_text = (
        "User: How do I read a file in Python?\n\n"
        "Assistant: Use open('file.txt', 'r') with a context manager.\n\n"
        "User: Thank you!"
    )
    adapter = ChatGPTAdapter()
    conv = adapter.normalize(paste_text, source=ConversationSource.PASTE)

    assert conv.source == ConversationSource.PASTE
    assert len(conv.messages) == 3
    assert conv.messages[0].role == ConversationRole.USER
    assert conv.messages[0].content == "How do I read a file in Python?"
    assert conv.messages[1].role == ConversationRole.ASSISTANT
    assert "context manager" in conv.messages[1].content
    assert conv.messages[2].role == ConversationRole.USER
    assert conv.messages[2].content == "Thank you!"


# ---------------------------------------------------------------------------
# 4. ADAPTER FACTORY
# ---------------------------------------------------------------------------

def test_adapter_factory():
    assert isinstance(get_adapter("chatgpt"), ChatGPTAdapter)
    assert isinstance(get_adapter("openai"), ChatGPTAdapter)
    assert isinstance(get_adapter("claude"), ClaudeAdapter)
    assert isinstance(get_adapter("anthropic"), ClaudeAdapter)
    assert isinstance(get_adapter("gemini"), GeminiAdapter)
    assert isinstance(get_adapter("google"), GeminiAdapter)

    with pytest.raises(ValueError, match="Unsupported conversation provider"):
        get_adapter("unknown_ai")


# ---------------------------------------------------------------------------
# 5. USER CONSENT BOUNDARY
# ---------------------------------------------------------------------------

def test_consent_boundary_mandatory_enforcement(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    service = ConversationIngestionService(db=db)

    # 1. Missing approval raises ConsentRequiredError
    unapproved_consent = ConversationConsent(
        consent_id="c_unapproved",
        approved=False,
        reason="User declined",
    )
    with pytest.raises(ConsentRequiredError, match="explicit user approval is mandatory"):
        service.ingest("claude", CLAUDE_EXPORT_FIXTURE, consent=unapproved_consent)

    # 2. Approved consent succeeds
    approved_consent = ConversationConsent(
        consent_id="c_approved",
        approved=True,
        reason="User clicked accept",
    )
    conv = service.ingest("claude", CLAUDE_EXPORT_FIXTURE, consent=approved_consent)
    assert conv.conversation_id == "claude_conv_001"

    # Verify consent was recorded in SQLite
    persisted_consent = db.get_conversation_consent("c_approved")
    assert persisted_consent is not None
    assert persisted_consent.approved is True


# ---------------------------------------------------------------------------
# 6. SECRET REDACTION IN CONVERSATION INGESTION
# ---------------------------------------------------------------------------

def test_secret_redaction_before_persistence(tmp_path: Path):
    db = Database(tmp_path / "test_secrets.db")
    service = ConversationIngestionService(db=db)

    raw_payload_with_secrets = {
        "id": "leak_conv",
        "messages": [
            {
                "role": "user",
                "content": "Here is my key: sk-live123456789012345678901234 and password = 'SuperSecretPassword123!'",
            },
            {
                "role": "assistant",
                "content": "Never share your Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 token.",
            },
        ],
    }

    consent = ConversationConsent(consent_id="c_ok", approved=True)
    conv = service.ingest("chatgpt", raw_payload_with_secrets, consent=consent)

    # 1. In-memory messages are redacted
    assert "sk-live123456789012345678901234" not in conv.messages[0].content
    assert "[REDACTED]" in conv.messages[0].content
    assert "SuperSecretPassword123!" not in conv.messages[0].content
    assert conv.messages[0].metadata.get("redacted") is True

    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in conv.messages[1].content
    assert "[REDACTED]" in conv.messages[1].content

    # 2. Database persisted messages are redacted
    stored = db.get_conversation("leak_conv")
    assert stored is not None
    assert "sk-live123456789012345678901234" not in stored.messages[0].content
    assert "SuperSecretPassword123!" not in stored.messages[0].content


# ---------------------------------------------------------------------------
# 7. SQLITE PERSISTENCE (SCHEMA V9)
# ---------------------------------------------------------------------------

def test_sqlite_persistence_and_retrieval(tmp_path: Path):
    from backend.domain.models import Project
    db = Database(tmp_path / "state.db")
    assert db.get_schema_version() >= 8

    # Insert project so foreign key constraint is satisfied
    db.upsert_project(Project(id="test_project_123", name="test_proj", root_path=str(tmp_path)))

    service = ConversationIngestionService(db=db)
    consent = ConversationConsent(consent_id="c_persist", approved=True)

    # Ingest conversation with project linking
    conv = service.ingest(
        "gemini",
        GEMINI_CONTENTS_FIXTURE,
        consent=consent,
        project_id="test_project_123",
        title="Project Linked Chat",
    )

    # Retrieve by ID
    loaded = db.get_conversation(conv.conversation_id)
    assert loaded is not None
    assert loaded.conversation_id == "gemini_conv_001"
    assert loaded.project_id == "test_project_123"
    assert loaded.title == "Project Linked Chat"
    assert len(loaded.messages) == 2
    assert loaded.messages[0].sequence == 1
    assert loaded.messages[1].sequence == 2

    # List conversations
    all_convs = db.list_conversations()
    assert len(all_convs) == 1

    project_convs = db.list_conversations(project_id="test_project_123")
    assert len(project_convs) == 1

    unlinked_convs = db.list_conversations(project_id="other_project")
    assert len(unlinked_convs) == 0

    # Delete conversation
    assert db.delete_conversation(conv.conversation_id) is True
    assert db.get_conversation(conv.conversation_id) is None


# ---------------------------------------------------------------------------
# 8. EDGE CASES AND MALFORMED INPUT
# ---------------------------------------------------------------------------

def test_malformed_and_empty_payloads():
    adapter = ChatGPTAdapter()

    # Empty payload
    c_empty = adapter.normalize("")
    assert c_empty.messages == []

    # Invalid JSON string
    c_invalid = adapter.normalize("random unformatted string without roles")
    assert c_invalid.messages == []

    # Unknown dict structure
    c_unknown = adapter.normalize({"random_key": [1, 2, 3]})
    assert c_unknown.messages == []


def test_missing_timestamp_not_invented():
    adapter = ClaudeAdapter()
    payload = {
        "messages": [
            {"role": "user", "content": "Question without timestamp"},
            {"role": "assistant", "content": "Answer without timestamp"},
        ]
    }
    conv = adapter.normalize(payload)
    assert conv.messages[0].timestamp is None
    assert conv.messages[1].timestamp is None


# ---------------------------------------------------------------------------
# 9. CLI INTEGRATION & JSON STDOUT PURITY
# ---------------------------------------------------------------------------

def test_cli_conversation_normalize_json(tmp_path: Path):
    fixture_file = tmp_path / "chat.json"
    fixture_file.write_text(json.dumps(CLAUDE_EXPORT_FIXTURE), encoding="utf-8")

    argv = [
        "conversation", "normalize",
        "--provider", "claude",
        "--file", str(fixture_file),
        "--consent",
        "--json",
    ]

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(argv)

    assert rc == 0
    raw_output = buf.getvalue().strip()
    data = json.loads(raw_output)

    assert data["status"] == "success"
    assert data["command"] == "conversation"
    assert data["action"] == "normalize"
    assert data["data"]["status"] == "normalized"
    assert data["data"]["total_messages"] == 2


def test_cli_conversation_import_json(tmp_path: Path):
    fixture_file = tmp_path / "gemini.json"
    fixture_file.write_text(json.dumps(GEMINI_CONTENTS_FIXTURE), encoding="utf-8")

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "pyproject.toml").write_text('[project]\nname="test"\nversion="1.0"\n', encoding="utf-8")

    argv = [
        "--project-root", str(repo_dir),
        "conversation", "import",
        "--provider", "gemini",
        "--file", str(fixture_file),
        "--consent",
        "--json",
    ]

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(argv)

    assert rc == 0
    raw_output = buf.getvalue().strip()
    data = json.loads(raw_output)

    assert data["status"] == "success"
    assert data["command"] == "conversation"
    assert data["action"] == "import"
    assert data["data"]["status"] == "imported"
    assert data["data"]["total_messages"] == 2


def test_cli_conversation_import_rejection_without_consent(tmp_path: Path):
    fixture_file = tmp_path / "unapproved.json"
    fixture_file.write_text(json.dumps(GEMINI_CONTENTS_FIXTURE), encoding="utf-8")

    repo_dir = tmp_path / "repo2"
    repo_dir.mkdir()
    (repo_dir / "pyproject.toml").write_text('[project]\nname="test"\nversion="1.0"\n', encoding="utf-8")

    # Call WITHOUT --consent flag
    argv = [
        "--project-root", str(repo_dir),
        "conversation", "import",
        "--provider", "gemini",
        "--file", str(fixture_file),
        "--json",
    ]

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = main(argv)

    assert rc == 2
    raw_output = buf.getvalue().strip()
    data = json.loads(raw_output)

    assert data["status"] == "error"
    assert "CONVERSATION_IMPORT_ERROR" in data["error"]["code"]
    assert "explicit user approval is mandatory" in data["error"]["message"]
