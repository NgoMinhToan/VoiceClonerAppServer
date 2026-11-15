from unittest.mock import MagicMock

from sqlalchemy import create_engine, inspect

from VoiceClonerAppServer.core.database import _migrate_schema
from VoiceClonerAppServer.models.db_models import Base


def test_new_tables_and_bonus_column_exist():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    names = set(inspect(engine).get_table_names())
    assert {"anonymous_users", "anonymous_daily_usages", "ad_reward_claims", "bonus_char_ledger"} <= names
    user_cols = {c["name"] for c in inspect(engine).get_columns("users")}
    assert "bonus_tts_chars" in user_cols
    anon_cols = {c["name"] for c in inspect(engine).get_columns("anonymous_users")}
    assert {"install_id", "bonus_tts_chars", "max_cloned_slots"} <= anon_cols


def test_migrate_schema_adds_bonus_tts_chars_for_postgresql():
    mock_conn = MagicMock()
    mock_conn.engine.url = "postgresql+asyncpg://postgres:secret@postgres:5432/voicecloner"

    _migrate_schema(mock_conn)

    calls = [str(c[0][0]) for c in mock_conn.execute.call_args_list]
    assert any(
        "ADD COLUMN IF NOT EXISTS bonus_tts_chars INTEGER NOT NULL DEFAULT 0" in call for call in calls
    ), "Missing bonus_tts_chars migration for PostgreSQL"


def test_migrate_schema_adds_bonus_tts_chars_for_sqlite():
    mock_conn = MagicMock()
    mock_conn.engine.url = "sqlite+aiosqlite:///storage/voicecloner.db"
    mock_conn.execute.return_value.fetchall.return_value = [
        (0, "id", "TEXT", 1, None, 1),
    ]
    mock_conn.execute.return_value.fetchone.return_value = None

    _migrate_schema(mock_conn)

    calls = [str(c[0][0]) for c in mock_conn.execute.call_args_list]
    assert any(
        "ADD COLUMN bonus_tts_chars INTEGER NOT NULL DEFAULT 0" in call for call in calls
    ), "Missing bonus_tts_chars migration for SQLite"
