import pytest
from unittest.mock import MagicMock
from sqlalchemy import text
from VoiceClonerAppServer.core.database import _migrate_schema


def test_migrate_schema_handles_postgresql():
    mock_conn = MagicMock()
    mock_conn.engine.url = "postgresql+asyncpg://postgres:secret@postgres:5432/voicecloner"

    _migrate_schema(mock_conn)

    # Verify that ALTER TABLE users ADD COLUMN IF NOT EXISTS was called 6 times
    calls = [str(c[0][0]) for c in mock_conn.execute.call_args_list]
    for col in (
        "daily_tts_char_limit",
        "rate_limit_requests_per_minute",
        "max_speech_char_limit",
        "max_preview_char_limit",
        "max_concurrent_tts",
        "max_queue_waiters",
    ):
        assert any(f"ADD COLUMN IF NOT EXISTS {col}" in call for call in calls), f"Missing {col} in calls"


def test_migrate_schema_handles_sqlite():
    mock_conn = MagicMock()
    mock_conn.engine.url = "sqlite+aiosqlite:///storage/voicecloner.db"
    # Mock PRAGMA table_info returning only id and email
    mock_conn.execute.return_value.fetchall.return_value = [
        (0, "id", "TEXT", 1, None, 1),
        (1, "email", "TEXT", 1, None, 0),
    ]
    mock_conn.execute.return_value.fetchone.return_value = None

    _migrate_schema(mock_conn)

    calls = [str(c[0][0]) for c in mock_conn.execute.call_args_list]
    for col in (
        "daily_tts_char_limit",
        "rate_limit_requests_per_minute",
        "max_speech_char_limit",
        "max_preview_char_limit",
        "max_concurrent_tts",
        "max_queue_waiters",
    ):
        assert any(f"ADD COLUMN {col}" in call for call in calls), f"Missing {col} in calls"
