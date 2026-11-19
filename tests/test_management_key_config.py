import json

from VoiceClonerAppServer.core.settings_manager import SettingsManager


def _write_config(tmp_path, key):
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"security": {"management_key": key}}), encoding="utf-8")
    return cfg


def test_management_key_default_is_empty_and_source_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("MANAGEMENT_KEY", raising=False)
    cfg = _write_config(tmp_path, "")
    sm = SettingsManager(settings_path=cfg)
    assert sm.get_settings().security.management_key == ""
    assert sm.get_effective_management_key() == ""
    assert sm.management_key_source == "unset"


def test_env_key_overrides_file_and_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGEMENT_KEY", "env_secret_key")
    cfg = _write_config(tmp_path, "file_secret_key")
    sm = SettingsManager(settings_path=cfg)

    assert sm.get_effective_management_key() == "env_secret_key"
    assert sm.get_settings().security.management_key == "env_secret_key"
    assert sm.management_key_source == "environment"

    sm.update_settings({"server": {"port": 8010}})
    on_disk = json.loads(cfg.read_text(encoding="utf-8"))
    assert on_disk["security"]["management_key"] == "file_secret_key"


def test_config_key_reports_config_source(tmp_path, monkeypatch):
    monkeypatch.delenv("MANAGEMENT_KEY", raising=False)
    cfg = _write_config(tmp_path, "file_secret_key")
    sm = SettingsManager(settings_path=cfg)
    assert sm.get_effective_management_key() == "file_secret_key"
    assert sm.management_key_source == "config"


def test_whitespace_only_config_key_counts_as_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("MANAGEMENT_KEY", raising=False)
    cfg = _write_config(tmp_path, "   \t  ")
    sm = SettingsManager(settings_path=cfg)
    assert sm.get_effective_management_key() == ""
    assert sm.management_key_source == "unset"


def test_legacy_default_key_in_config_is_treated_as_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("MANAGEMENT_KEY", raising=False)
    cfg = _write_config(tmp_path, "vc_admin_secret_key_123")
    sm = SettingsManager(settings_path=cfg)

    assert sm.get_effective_management_key() == ""
    assert sm.management_key_source == "unset"
    assert sm.get_settings().security.management_key == ""


def test_legacy_default_key_in_environment_is_treated_as_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("MANAGEMENT_KEY", "vc_admin_secret_key_123")
    cfg = _write_config(tmp_path, "")
    sm = SettingsManager(settings_path=cfg)

    assert sm.get_effective_management_key() == ""
    assert sm.management_key_source == "unset"
